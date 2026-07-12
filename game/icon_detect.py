""" CNN-based icon detector: locates popup close/return buttons on game screenshots.
Used by automation to dismiss popup windows that block the main menu (e.g. announcements,
events, rankings) while waiting to auto-join a game.

Design: a tiny fully-convolutional patch classifier (IconDetNet, ~90k params) is trained on
icon crops from training_data/popups (see scripts/train_icon_detector.py). At inference the
screenshot is resized to a fixed reference resolution (960x540) and the net is applied
convolutionally at several scales, producing per-class probability heatmaps; peaks above a
confidence threshold become detections. Coordinates are returned in the same 16x9 units used
by game.automation.Positions, so results can be clicked directly."""

from dataclasses import dataclass
import io
import os
import threading

from PIL import Image
import numpy as np
import torch
from torch import nn

from common.log_helper import LOGGER
from common.utils import Folder, sub_file

# ---- geometry / normalization contract, shared by training and inference ----
REF_W, REF_H = 960, 540         # reference resolution all detection happens at (16:9)
PATCH = 56                      # training patch size == receptive field of IconDetNet
STRIDE = 8                      # output stride of IconDetNet
NORM_MEAN = 0.5                 # input normalization: (x/255 - mean) / std
NORM_STD = 0.25
ICON_CLASSES = ["close_x", "return_arrow"]   # heatmap channels 1..N; channel 0 = background

MODEL_FILE = "icon_detector.pth"             # lives in resources/ so it ships with the app
SCALES = (0.8, 1.0, 1.25)                    # multi-scale inference (icon size tolerance)
NMS_DIST_PX = 40                             # min distance between detections, at REF scale


class IconDetNet(nn.Module):
    """ Small FCN: patch classifier trained on PATCH-sized crops, applied fully-convolutionally.
    Receptive field PATCH(56)px, output stride STRIDE(8)."""
    def __init__(self, n_out:int=len(ICON_CLASSES) + 1):
        super().__init__()
        def block(cin, cout, k, pool):
            layers = [nn.Conv2d(cin, cout, k, padding=k // 2), nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]
            if pool:
                layers.append(nn.MaxPool2d(2))
            return layers
        self.features = nn.Sequential(
            *block(3, 24, 5, True),        # /2
            *block(24, 32, 3, True),       # /4
            *block(32, 48, 3, True),       # /8
            *block(48, 64, 3, False),
            *block(64, 64, 3, False),
        )
        self.classifier = nn.Conv2d(64, n_out, 1)

    def forward(self, x:torch.Tensor) -> torch.Tensor:
        """ input (B,3,H,W) normalized; output logits (B, n_out, H/8, W/8)"""
        return self.classifier(self.features(x))


@dataclass
class IconDetection:
    """ one detected icon on screen"""
    icon:str        # class name, e.g. "close_x"
    x:float         # center position in 16x9 game units (same system as automation.Positions)
    y:float
    score:float     # confidence 0..1


class IconDetector:
    """ loads the trained model and finds popup icons in screenshots.
    Degrades gracefully: if the model file is absent/corrupt, available() is False
    and detect() returns []."""

    def __init__(self, model_file:str=None):
        self._lock = threading.Lock()   # detect() may be called from automation task threads
        self._model:IconDetNet = None
        self.classes:list[str] = ICON_CLASSES
        file = model_file or sub_file(Folder.RES, MODEL_FILE)
        if not os.path.isfile(file):
            LOGGER.warning("Icon detector model not found (%s). Popup dismissal disabled. "
                           "Train one with scripts/train_icon_detector.py", file)
            return
        try:
            ckpt = torch.load(file, map_location="cpu")
            self.classes = ckpt.get("classes", ICON_CLASSES)
            model = IconDetNet(n_out=len(self.classes) + 1)
            model.load_state_dict(ckpt["state_dict"])
            model.eval()
            self._model = model
            LOGGER.info("Icon detector loaded (%s, classes=%s)", os.path.basename(file), self.classes)
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.error("Error loading icon detector %s: %s. Popup dismissal disabled.", file, e)

    def available(self) -> bool:
        """ True if the model loaded and detect() can work"""
        return self._model is not None

    def detect(self, img_bytes:bytes, threshold:float=0.8) -> list[IconDetection]:
        """ find icons in a page screenshot.
        params:
            img_bytes: PNG/JPEG bytes as returned by GameBrowser.screen_shot()
            threshold: min class probability for a detection
        returns:
            detections sorted by score descending, coordinates in 16x9 units"""
        if self._model is None or img_bytes is None:
            return []
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.warning("Icon detect: cannot decode screenshot: %s", e)
            return []
        # resizing to REF absorbs viewport size and browser zoom (devicePixelRatio)
        img = img.resize((REF_W, REF_H), Image.Resampling.LANCZOS)
        peaks:list[IconDetection] = []      # candidates across scales, coords at REF scale (px)
        with self._lock, torch.no_grad():
            for scale in SCALES:
                w, h = round(REF_W * scale), round(REF_H * scale)
                arr = np.asarray(img.resize((w, h), Image.Resampling.LANCZOS), dtype=np.float32)
                x = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
                x = (x / 255.0 - NORM_MEAN) / NORM_STD
                prob = torch.softmax(self._model(x), dim=1)[0]          # (C+1, h/8, w/8)
                peaks += self._heatmap_peaks(prob, scale, threshold)
        dets = self._nms(peaks)
        for d in dets:  # REF pixels -> 16x9 units
            d.x = d.x / (REF_W / 16)
            d.y = d.y / (REF_H / 9)
        return dets

    def _heatmap_peaks(self, prob:torch.Tensor, scale:float, threshold:float) -> list[IconDetection]:
        """ extract local maxima above threshold from class heatmaps.
        returns candidates with x,y in REF-scale pixels"""
        found = []
        for ci, cname in enumerate(self.classes):
            hm = prob[ci + 1]                                   # channel 0 is background
            mask = hm >= threshold
            if not bool(mask.any()):
                continue
            hm_np = hm.numpy()
            ys, xs = np.nonzero(mask.numpy())
            for i, j in zip(ys, xs):
                # local maximum in 3x3 neighborhood only, to avoid duplicate blob points
                y0, y1 = max(0, i - 1), min(hm_np.shape[0], i + 2)
                x0, x1 = max(0, j - 1), min(hm_np.shape[1], j + 2)
                neigh = hm_np[y0:y1, x0:x1]
                if hm_np[i, j] < neigh.max():
                    continue
                # score-weighted centroid of the neighborhood for sub-cell accuracy
                wsum = neigh.sum()
                gy, gx = np.mgrid[y0:y1, x0:x1]
                cy = float((gy * neigh).sum() / wsum)
                cx = float((gx * neigh).sum() / wsum)
                px = (cx + 0.5) * STRIDE / scale                # heatmap cell -> input px -> REF px
                py = (cy + 0.5) * STRIDE / scale
                found.append(IconDetection(cname, px, py, float(hm_np[i, j])))
        return found

    @staticmethod
    def _nms(peaks:list[IconDetection]) -> list[IconDetection]:
        """ greedy distance-based non-maximum suppression across classes and scales"""
        peaks = sorted(peaks, key=lambda d: d.score, reverse=True)
        kept:list[IconDetection] = []
        for p in peaks:
            if all((p.x - k.x) ** 2 + (p.y - k.y) ** 2 >= NMS_DIST_PX ** 2 for k in kept):
                kept.append(p)
        return kept
