# PyFireCA timber heritage study

This repository contains the source code for the Pingyan and Batuan timber-village firebreak study. The study version includes the V3 thermal solver, the wind-radiation and terminal clocks, the fixed-layout experiment runners, the paired-loss summaries, and the manuscript figure renderer.

## Environment

Use Python 3.11 or later and a C++17 compiler on the path for the native terminal backend. Install the project with its geospatial and development dependencies and add Matplotlib for the figure scripts.

```sh
python -m pip install -e '.[dev,gis,village,village-vision,planning]' matplotlib
```

## Reproduction entry points

| Stage | Repository code |
|---|---|
| Position controls | [prepare position controls](../scripts/prepare_heritage_position_controls_20261103.py), [run fixed comparisons](../scripts/run_heritage_fixed_comparison.py), [summarize position controls](../scripts/summarize_heritage_followup_20261103.py) |
| Development wind trials and radius check | [prepare wind strategies](../scripts/prepare_heritage_wind_strategies.py), [run wind strategies](../scripts/run_heritage_wind_strategies.py), [continue to extinction](../scripts/run_heritage_wind_extinction.py) |
| Held-out layout comparisons | [prepare the held-out panel](../scripts/prepare_heritage_wind_validation.py), [run five fixed layouts](../scripts/run_heritage_wind_validation.py), [summarize the terminal runs](../scripts/summarize_heritage_wind_validation.py) |
| Compact layout comparison | [run paired geometry validation](../scripts/run_heritage_geometry_validation.py), [recover and verify the registered pairs](../scripts/resume_heritage_geometry_validation.py), [summarize the paired results](../scripts/summarize_heritage_geometry_validation.py) |
| Numerical implementation | [extinction certificate](../src/pyfireca/village/extinction_v3.py), [wind radiation](../scripts/heritage_wind_radiation.py), [native thermal kernel](../scripts/heritage_wind_terminal_kernel.cpp) |
| Figures | [render manuscript figures](../scripts/render_npj_heritage_outline_figures.py) |

Use the manuscript's cited Git commit to identify the exact code. The study configurations under `examples/research/heritage/` refer to the study inputs and run archives. Study rasters, building inputs, registered case bundles and generated case results require a separate study-data bundle. To reproduce figures, place the matching archives and prepared inputs at the paths documented in the experiment registrations. Resolve any recorded local paths against the supplied case files.

To run the focused checks with the repository's test fixtures:

```sh
python -m pytest -q tests/planning/test_heritage_wind_validation.py tests/planning/test_wind_terminal_clock.py tests/planning/test_wind_validation_clock.py tests/village/test_extinction_v3.py
```
