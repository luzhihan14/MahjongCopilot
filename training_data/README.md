# Training data for the popup-dismiss icon detector

Data used to train the small CNN that finds close/return buttons on Majsoul
popup windows (used by auto-join to dismiss popups blocking the main menu).

## Layout

```
training_data/popups/
├── raw/          <- DROP YOUR SNAPSHOTS HERE: full-screen shots of popups
│                    (gitignored: may contain account name/rank)
├── negatives/    <- full-screen shots WITHOUT any popup (plain main menu,
│                    shop open, in-game, ...) — background / hard negatives
│                    (gitignored, same reason)
└── icons/        <- cropped icon patches, one folder per class (tracked in git)
    ├── close_x/        the ✕ button on popups (red diamond, plain gray, ...)
    ├── return_arrow/   the curved return/back arrow (all colorways)
    └── confirm_ok/     placeholder for a future class (not trained yet)
```

The classes actually trained are listed in `popups/labels.json` (`"classes"`), which must
match `ICON_CLASSES` in `game/icon_detect.py`. Currently: `close_x`, `return_arrow`.
The icon crops are for human reference; training samples are cut from `raw/` using the
bounding boxes in `labels.json` (canvas-relative pixels; `canvas_xywh` locates the game
canvas inside the raw window screenshots).

## What to put in `raw/`

- One (or more) full-screen snapshot per popup type you want dismissed.
- Prefer screenshots of the game browser window itself. Any resolution is
  fine — training augments over a 0.7–1.3× scale range and the detector
  searches multiple scales at runtime — but shots at the resolution you
  actually play at (`browser_width` × `browser_height` in settings.json)
  match best.
- Different client languages / event skins of the same popup are welcome:
  more variety → better generalization (this is why a CNN over raw
  template matching).

## What happens with it

1. Icons are cropped out of `raw/` into `icons/<class>/` (small PNGs,
   these are committed — they contain no personal info).
2. `scripts/train_icon_detector.py` trains a tiny fully-convolutional
   classifier (positives = `icons/`, negatives sampled from `negatives/`
   and non-icon regions of `raw/`), with scale/brightness/rotation
   augmentation, and writes weights to `models/icon_detector.pth`.
3. At runtime `game/icon_detect.py` slides the net over a screenshot and
   returns `(class, x, y, confidence)` for the auto-join popup dismisser.

Adding a new popup type later = drop its snapshot in `raw/`, crop its
button into the right `icons/` class (or a new class folder), re-run the
training script.
