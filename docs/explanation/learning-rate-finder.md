# Finding the learning rate from the data

The PyTorch models of `corvus-eval` (`raw_hier_classifier`, `raw_hier_anomaly`, `gru_autoencoder`,
`cnn1d`) can pick their learning rate from the training data instead of using a guessed constant.
This page explains the method, the formulas and how far to trust the result. How to switch it on is
in [evaluate-models.md](../how-to/evaluate-models.md#learning-rate-auto). The code is
`ml/src/corvus_ml/eval/lr.py`.

## Why a range test

The best learning rate depends on the model, the loss, the batch size and the data, and it moves when
any of them changes. Too small wastes epochs, too large diverges. Leslie N. Smith's *LR range test*
(["Cyclical Learning Rates for Training Neural Networks"](https://arxiv.org/abs/1506.01186),
section 3.3) measures it: train briefly while the learning rate grows, and watch where the loss
starts to fall and where it breaks. The exponential variant used here is the one popularised by
fastai's `lr_find`.

## The sweep

On a **deep copy** of the model (the model that is trained afterwards is never touched), with its own
AdamW, a seeded subset of at most `steps * batch_size` training samples and `torch.random.fork_rng`
(the global RNG is restored), the learning rate of step `i` is

```
lr_i = lr_min * (lr_max / lr_min) ^ (i / (N - 1))        i = 0 .. N-1
```

so the first step uses `lr_min` and the last `lr_max` (defaults `1e-7` and `1`, `N = 60`; `lr_steps`
changes `N`, 100 gives a smoother curve). One optimizer step is taken per batch, with the same
gradient clipping (1.0) as the training loop. The same code serves any objective: it only needs a
`loss_fn(model, batch)` closure, so the supervised classifier (BCE) and the masked objectives of the
anomaly model and of the pretraining use it unchanged.

## Smoothing and early stop

Single-batch losses are noisy, so the curve is an exponential moving average with bias correction:

```
a_t = beta * a_(t-1) + (1 - beta) * x_t,   a_0 = 0
s_t = a_t / (1 - beta^t)                    beta = 0.9
```

The sweep stops as soon as `s_t > 4 * min(s)` (divergence) or when a loss is not finite. The report
says which of the two ended it.

## Two suggestion rules

Both read the smoothed curve `s(lr)`; the result carries both numbers.

| Rule | Definition | Character |
| --- | --- | --- |
| `valley` (default) | `lr` at the minimum of `s`, divided by 10 | conservative: the minimum itself sits on the edge of divergence |
| `steepest` | `lr` with the most negative `d s / d log10(lr)`, searched on the part of the curve up to the minimum | bolder: the middle of the descent |

The slope of `steepest` is the secant over `i - h .. i + h` with `h = max(1, round(0.05 N))`,
because a one-step difference mistakes the noise of small batches for a steep descent. `valley` is
the default because it is the more robust choice on the small, noisy datasets Corvus trains on;
select the other with `lr_rule = "steepest"`. If a rule finds nothing (no descent), the other one is
used, and if neither does, `fallback_lr` (`2e-3`); the report names the rule that was actually used.
The suggestion is clamped to `[lr_min, lr_max]`.

## From the suggestion to a schedule

The suggestion is the **peak** of the schedule. With `T = epochs * ceil(n_train / batch_size)`
optimizer steps and a warmup of `W = min(T // 2, clamp(round(0.05 * T), 5, 2000))` steps:

```
lr(s) = peak * s / W                                               s < W
lr(s) = floor + (peak - floor) * (1 + cos(pi * (s - W) / (T - W))) / 2     W <= s < T
lr(s) = floor                                                      s >= T
```

with `floor = 0.1 * peak`. The alternative `lr_schedule = "wsd"` (warmup-stable-decay) holds `peak`
until `0.8 * T` and then decays linearly to `floor`. A numeric `lr` keeps the schedule it always had
(10 % warmup, cosine to 0).

If the range test used another batch size than the training does, the suggestion is scaled by
`sqrt(batch / batch_range_test)`. That square-root rule is a heuristic for Adam-type optimizers
(linear scaling is the SGD rule); inside `corvus-eval` both batch sizes are equal, so it only
matters for callers of `plan_lr`.

## Optuna around the found value

With `lr = "auto"` the Optuna search no longer draws `lr` blindly from `3e-4 .. 1e-2`. It draws
`lr_mult` log-uniformly from `0.3 .. 3` and the training uses `lr_mult * suggestion` as the peak, so
the search spends its trials around a value that already works. Narrow it with
`[tune.space] lr_mult = [0.5, 2]`. Each trial runs its own range test (the model differs per trial).

## Reading the curve

`report.json` (`contexts.<ctx>.candidates.<name>.training.lr_plan`) and `corvus-eval lr-find` give
the curve as `[lr, smoothed loss]` pairs, downsampled to at most 50 points.

* A flat start, then a fall, a minimum and a sharp rise: a healthy curve. The suggestion sits on the
  fall, one decade before the rise.
* A curve that is flat or noisy everywhere (the total drop is no bigger than the wiggles) says that
  the subset is too small to see the effect of the learning rate. Read the suggestion as a guess.
* A curve that falls until `lr_max`: raise `lr_max`. A curve that rises from the start: `lr_min` is
  already too large, or the loss is wrong (check the closure).
* An early stop at a small learning rate with a very narrow valley often means large gradient noise;
  a larger batch size helps more than a smaller learning rate.

## Limits

* The suggestion is a starting point, not an optimum. It is measured on the first steps of training,
  where the loss falls fastest; later in training smaller rates usually work better, which is why
  the schedule decays.
* Tiny datasets (hundreds of windows) give noisy curves; the synthetic smoke data of
  `eval.synthetic-raw.toml` is an example (the classifier curve there has a total drop of about
  0.1 against wiggles of 0.07). Use more `lr_steps` or `lr = <number>` there.
* The model starts from random weights (or from the pretrained encoder), so the result depends on
  both; re-run when the architecture, the loss, the batch size or the amount of data changes. The
  eval harness does that on every `lr = "auto"` fit.
* The range test costs `lr_steps` optimizer steps per fit and per Optuna trial.
