""" Train the popup-icon detector (game/icon_detect.py) from training_data/popups.

Data: training_data/popups/labels.json defines the game-canvas region inside each raw
screenshot and the icon bounding boxes (canvas-relative, at 960x540 reference scale).
Positives are augmented crops around the labeled icons; negatives are sampled from the
rest of the screenshots, resources/mainmenu.png, anything in training_data/popups/negatives/,
plus mined hard negatives. The trained weights are written to resources/icon_detector.pth
(committed with the app; PyInstaller already bundles resources/).

Usage: venv/bin/python scripts/train_icon_detector.py [--epochs N] [--out FILE]
Runs on CPU in a few minutes. Deterministic (fixed seed).
"""
import argparse
import io
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# pylint: disable=wrong-import-position
from game.icon_detect import (IconDetNet, IconDetector, ICON_CLASSES, REF_W, REF_H,
                              PATCH, STRIDE, NORM_MEAN, NORM_STD)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "training_data" / "popups"
DEFAULT_OUT = ROOT / "resources" / "icon_detector.pth"

SEED = 42
BATCH = 64
BATCHES_PER_EPOCH = 50
POS_PER_CLASS = 12          # per batch; rest (~60%) is background
MINE_EVERY = 3              # hard-negative mining interval (epochs)
HARD_NEG_CAP = 2000
EVAL_THRESHOLD = 0.90       # keep in sync with the popup_dismiss_threshold default in Settings


def load_dataset():
    """ returns (canvases, icon_labels, neg_canvases)
    canvases: {file: np.uint8 HxWx3 at REF size} for labeled screenshots
    icon_labels: list of (file, class_idx0based, cx, cy, w, h) at REF scale
    neg_canvases: list of np arrays with no icons at all (pure negative sources)"""
    with open(DATA_DIR / "labels.json", encoding="utf-8") as f:
        labels = json.load(f)
    assert labels["classes"] == ICON_CLASSES, \
        f"labels.json classes {labels['classes']} != icon_detect.ICON_CLASSES {ICON_CLASSES}"
    cx0, cy0, cw, ch = labels["canvas_xywh"]
    canvases, icons = {}, []
    for shot in labels["screenshots"]:
        img = Image.open(DATA_DIR / "raw" / shot["file"]).convert("RGB")
        canvas = img.crop((cx0, cy0, cx0 + cw, cy0 + ch)).resize((REF_W, REF_H), Image.Resampling.LANCZOS)
        canvases[shot["file"]] = np.asarray(canvas, dtype=np.uint8)
        sx, sy = REF_W / cw, REF_H / ch
        for ic in shot["icons"]:
            x, y, w, h = ic["box_xywh"]
            icons.append((shot["file"], ICON_CLASSES.index(ic["class"]),
                          (x + w / 2) * sx, (y + h / 2) * sy, w * sx, h * sy))
    neg = []
    mainmenu = ROOT / "resources" / "mainmenu.png"
    if mainmenu.is_file():
        neg.append(np.asarray(Image.open(mainmenu).convert("RGB")
                              .resize((REF_W, REF_H), Image.Resampling.LANCZOS), dtype=np.uint8))
    neg_dir = DATA_DIR / "negatives"
    if neg_dir.is_dir():
        for p in sorted(neg_dir.iterdir()):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                im = Image.open(p).convert("RGB")
                # negatives may be raw window shots too: crop canvas region if same size as raws
                if (im.width, im.height) != (REF_W, REF_H) and im.width > cx0 + cw and im.height > cy0 + ch:
                    im = im.crop((cx0, cy0, cx0 + cw, cy0 + ch))
                neg.append(np.asarray(im.resize((REF_W, REF_H), Image.Resampling.LANCZOS), dtype=np.uint8))
    return canvases, icons, neg


# ---------------- patch extraction & augmentation ----------------

def extract_patch(img:np.ndarray, cx:float, cy:float, crop:float, rot_deg:float, rng:random.Random) -> np.ndarray:
    """ crop a (crop x crop) region centered at (cx,cy), rotate, resize to PATCH.
    out-of-bounds area is filled with a random uniform color (teaches padding invariance)."""
    margin = int(crop * 0.75)   # room for rotation
    x0, y0 = int(round(cx)) - margin, int(round(cy)) - margin
    size = margin * 2
    fill = tuple(rng.randint(0, 255) for _ in range(3)) if rng.random() < 0.5 else (0, 0, 0)
    region = Image.new("RGB", (size, size), fill)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(img.shape[1], x0 + size), min(img.shape[0], y0 + size)
    if sx1 > sx0 and sy1 > sy0:
        region.paste(Image.fromarray(img[sy0:sy1, sx0:sx1]), (sx0 - x0, sy0 - y0))
    if abs(rot_deg) > 0.1:
        region = region.rotate(rot_deg, resample=Image.Resampling.BILINEAR)
    c = size / 2
    region = region.crop((int(c - crop / 2), int(c - crop / 2), int(c + crop / 2), int(c + crop / 2)))
    region = region.resize((PATCH, PATCH), Image.Resampling.BILINEAR)
    return np.asarray(region, dtype=np.float32)


def photometric(patch:np.ndarray, rng:random.Random) -> np.ndarray:
    """ random brightness / contrast / per-channel gain / noise, in-place-ish on float array"""
    patch = patch * rng.uniform(0.7, 1.3)                                   # brightness
    mean = patch.mean()
    patch = (patch - mean) * rng.uniform(0.75, 1.3) + mean                  # contrast
    patch = patch * np.array([rng.uniform(0.85, 1.15) for _ in range(3)],
                             dtype=np.float32)                              # channel gain (~hue/sat)
    if rng.random() < 0.7:
        patch = patch + np.random.default_rng(rng.getrandbits(32)).normal(0, rng.uniform(1, 6), patch.shape)
    return np.clip(patch, 0, 255)


class Sampler:
    """ generates training batches on the fly"""
    def __init__(self, canvases, icons, neg_canvases, rng:random.Random):
        self.canvases = canvases
        self.icons = icons
        self.neg = neg_canvases
        self.rng = rng
        self.hard_negs:list[np.ndarray] = []
        self.by_class = {ci: [ic for ic in icons if ic[1] == ci] for ci in range(len(ICON_CLASSES))}
        # negative sources: all labeled canvases (minus icon regions) + pure negative canvases
        self.neg_sources = [(arr, [ic for ic in icons if ic[0] == f]) for f, arr in canvases.items()]
        self.neg_sources += [(arr, []) for arr in neg_canvases]

    def _positive(self, class_idx:int) -> np.ndarray:
        f, _, cx, cy, _, _ = self.rng.choice(self.by_class[class_idx])
        img = self.canvases[f]
        crop = self.rng.uniform(PATCH / 1.35, PATCH / 0.7)
        jitter = crop * 0.12
        px = cx + self.rng.uniform(-jitter, jitter)
        py = cy + self.rng.uniform(-jitter, jitter)
        return extract_patch(img, px, py, crop, self.rng.uniform(-6, 6), self.rng)

    def _negative(self) -> np.ndarray:
        r = self.rng.random()
        if self.hard_negs and r < 0.4:                      # mined hard negatives
            return photometric(self.hard_negs[self.rng.randrange(len(self.hard_negs))].copy(), self.rng)
        img, img_icons = self.neg_sources[self.rng.randrange(len(self.neg_sources))]
        if img_icons and r < 0.55:                          # offset negatives: near, but not on, an icon
            _, _, cx, cy, w, h = self.rng.choice(img_icons)
            rad = self.rng.uniform(max(w, h) * 0.8, max(w, h) * 1.8)
            ang = self.rng.uniform(0, 2 * np.pi)
            px, py = cx + rad * np.cos(ang), cy + rad * np.sin(ang)
        else:                                               # uniform random, away from all icons
            for _ in range(50):
                px = self.rng.uniform(0, REF_W)
                py = self.rng.uniform(0, REF_H)
                if all((px - ic[2]) ** 2 + (py - ic[3]) ** 2 > (max(ic[4], ic[5]) * 0.8) ** 2
                       for ic in img_icons):
                    break
        crop = self.rng.uniform(PATCH / 1.35, PATCH / 0.7)
        # photometric here too: augmentation artifacts must not become a positive-class cue
        return photometric(extract_patch(img, px, py, crop, self.rng.uniform(-6, 6), self.rng), self.rng)

    def batch(self) -> tuple[torch.Tensor, torch.Tensor]:
        """ one (x, y) training batch"""
        patches, targets = [], []
        for ci in range(len(ICON_CLASSES)):
            for _ in range(POS_PER_CLASS):
                patches.append(photometric(self._positive(ci), self.rng))
                targets.append(ci + 1)
        while len(patches) < BATCH:
            patches.append(self._negative())
            targets.append(0)
        x = torch.from_numpy(np.stack(patches).astype(np.float32)).permute(0, 3, 1, 2)
        x = (x / 255.0 - NORM_MEAN) / NORM_STD
        return x, torch.tensor(targets, dtype=torch.long)

    def mine_hard_negatives(self, model:IconDetNet):
        """ run the FCN over negative sources at the inference scales; collect confident
        false-positive patches. Mining at multiple scales matters: FPs are often
        scale-dependent (an ornament may only resemble an icon when shrunk/enlarged)."""
        model.eval()
        added = 0
        with torch.no_grad():
            for img, img_icons in self.neg_sources:
                for scale in (0.75, 1.0, 1.25):
                    if scale != 1.0:
                        w, h = round(REF_W * scale), round(REF_H * scale)
                        arr = np.asarray(Image.fromarray(img).resize((w, h), Image.Resampling.LANCZOS),
                                         dtype=np.float32)
                    else:
                        arr = img.astype(np.float32)
                    x = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
                    x = (x / 255.0 - NORM_MEAN) / NORM_STD
                    prob = torch.softmax(model(x), dim=1)[0]
                    fg = prob[1:].max(dim=0).values.numpy()     # max icon-class prob per cell
                    ys, xs = np.nonzero(fg > 0.2)
                    order = np.argsort(-fg[ys, xs])[:40]        # top offenders per image+scale
                    for k in order:
                        px = (xs[k] + 0.5) * STRIDE / scale     # back to REF coords
                        py = (ys[k] + 0.5) * STRIDE / scale
                        if any((px - ic[2]) ** 2 + (py - ic[3]) ** 2 < (max(ic[4], ic[5]) * 0.8) ** 2
                               for ic in img_icons):
                            continue                            # actually an icon, skip
                        # crop PATCH/scale so the stored patch shows what the net saw at this scale
                        self.hard_negs.append(extract_patch(img, px, py, PATCH / scale, 0, self.rng))
                        added += 1
        del self.hard_negs[:max(0, len(self.hard_negs) - HARD_NEG_CAP)]
        model.train()
        return added


# ---------------- training ----------------

def train(epochs:int, out_file:Path):
    """ full training run + evaluation"""
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    rng = random.Random(SEED)

    canvases, icons, neg = load_dataset()
    print(f"dataset: {len(canvases)} labeled shots, {len(icons)} icons, {len(neg)} negative-only images")
    sampler = Sampler(canvases, icons, neg, rng)
    model = IconDetNet()
    model.train()
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs * BATCHES_PER_EPOCH)
    lossfn = nn.CrossEntropyLoss(label_smoothing=0.05)

    t0 = time.time()
    for ep in range(1, epochs + 1):
        ep_loss, ep_acc = 0.0, 0.0
        for _ in range(BATCHES_PER_EPOCH):
            x, y = sampler.batch()
            # supervise the CENTER output cell: it is the only cell whose receptive field matches
            # the labeled patch; corner cells of the 7x7 map sit ~24px off-target in zero padding,
            # and averaging them in blurs the localization signal the FCN needs at inference.
            center = PATCH // STRIDE // 2
            logits = model(x)[:, :, center, center]
            loss = lossfn(logits, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            ep_loss += loss.item()
            ep_acc += (logits.argmax(1) == y).float().mean().item()
        msg = (f"epoch {ep:3d}/{epochs}  loss {ep_loss / BATCHES_PER_EPOCH:.4f}  "
               f"acc {ep_acc / BATCHES_PER_EPOCH:.3f}  ({time.time() - t0:.0f}s)")
        if ep % MINE_EVERY == 0 and ep < epochs:
            mined = sampler.mine_hard_negatives(model)
            msg += f"  mined {mined} hard negs (pool {len(sampler.hard_negs)})"
        print(msg)

    model.eval()
    ckpt = {
        "state_dict": model.state_dict(),
        "classes": ICON_CLASSES,
        "ref_size": [REF_W, REF_H],
        "patch": PATCH,
        "stride": STRIDE,
        "norm": [NORM_MEAN, NORM_STD],
        "seed": SEED,
        "epochs": epochs,
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    torch.save(ckpt, out_file)
    size_kb = out_file.stat().st_size / 1024
    print(f"\nsaved {out_file} ({size_kb:.0f} KB)")
    evaluate(out_file, canvases, icons, neg)


def evaluate(model_file:Path, canvases, icons, neg):
    """ end-to-end eval through the runtime IconDetector on the exact inference path"""
    det = IconDetector(str(model_file))
    assert det.available()
    print(f"\n=== evaluation (threshold {EVAL_THRESHOLD}) ===")
    errors = 0
    for f, arr in canvases.items():
        expected = [ic for ic in icons if ic[0] == f]
        for variant_name, img in [("1.00x", arr), ("0.75x", _rescaled(arr, 0.75)), ("1.25x", _rescaled(arr, 1.25))]:
            dets = det.detect(_png_bytes(img), EVAL_THRESHOLD)
            ok, msgs = _check(dets, expected)
            errors += 0 if ok else 1
            status = "OK " if ok else "FAIL"
            print(f"{status} {f:14s} {variant_name}: {'; '.join(msgs)}")
    for i, arr in enumerate(neg):
        dets = det.detect(_png_bytes(arr), EVAL_THRESHOLD)
        status = "OK " if not dets else "FAIL"
        errors += 1 if dets else 0
        extra = "; ".join(f"FP {d.icon}@({d.x:.1f},{d.y:.1f}) p={d.score:.2f}" for d in dets) or "no detections"
        print(f"{status} negative[{i}] (mainmenu/clean): {extra}")
    print(f"\n{'ALL PASS' if errors == 0 else f'{errors} FAILURES'}")
    return errors


def _rescaled(arr:np.ndarray, factor:float) -> np.ndarray:
    """ simulate a different viewport resolution: rescale then let detect() resize back to REF"""
    im = Image.fromarray(arr)
    im = im.resize((round(REF_W * factor), round(REF_H * factor)), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def _png_bytes(arr:np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def _check(dets, expected) -> tuple[bool, list[str]]:
    """ every labeled icon found within half its size, no spurious extra detections"""
    msgs, ok = [], True
    for _, ci, cx, cy, w, h in expected:
        ux, uy = cx / (REF_W / 16), cy / (REF_H / 9)        # label center in 16x9 units
        tol = max(w, h) * 0.5 / (REF_W / 16)
        match = [d for d in dets if d.icon == ICON_CLASSES[ci]
                 and abs(d.x - ux) < tol and abs(d.y - uy) < max(w, h) * 0.5 / (REF_H / 9)]
        if match:
            d = match[0]
            msgs.append(f"{d.icon} found ({d.x:.2f},{d.y:.2f})u p={d.score:.2f}")
        else:
            ok = False
            msgs.append(f"{ICON_CLASSES[ci]} MISSED (expected ~({ux:.2f},{uy:.2f})u)")
    extras = len(dets) - sum(1 for _, ci, cx, cy, w, h in expected)
    if extras > 0:
        ok = False
        msgs.append(f"{extras} spurious detection(s): " +
                    "; ".join(f"{d.icon}@({d.x:.1f},{d.y:.1f}) p={d.score:.2f}" for d in dets))
    return ok, msgs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    train(args.epochs, args.out)
