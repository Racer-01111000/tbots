# V3R notes
* **Discarded smoke run (disclosed).** Before the freeze I ran `gym.batch_v3` locally to measure runtime (3 generations on training worlds of experiment id `SYNGYM-20261005-V3`, then one `--final` pass that executed validation once; result: nominee failed validation, lower-quartile excess -0.77 pp). Nothing was tuned on it and the output (in /tmp) was deleted from consideration. To keep the production validation set genuinely unopened, the experiment id was changed to `SYNGYM-20261005-V3R`, which re-derives every world seed.
* Training bank = 12 worlds reused every batch (designated training data); validation 6 and sealed 6 are fresh and untouched.
* Evolution is price-only (TREND and D families); indicator-aware strategies stay in the EVT-V2 experiment.
* The week's program: batches Mon-Fri 05:00 ET, generations continue across batches; the Friday batch runs `--final`.
