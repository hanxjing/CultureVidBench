# CultureVidBench: Benchmarking Cultural Understanding in Text-to-Video Generation

[Project page](https://hanxjing.github.io/CultureVidBench/) | [Paper](https://arxiv.org/abs/2608.01942)

This repository contains the prompt list of CultureVidBench and `evaluate_gemini.py`, the script that uses Gemini as an automatic judge to score AI-generated videos on cultural faithfulness, text, audio, semantic alignment, and general quality.

```
eval/
├── evaluate_gemini.py
├── evaluation_criteria.json
├── prompts/
│   └── index_elements_prompts.txt   # prompt list (1,000 prompts)
└── video_generate/
    ├── <generation_model>/
    │   └── <video_id>_*.mp4
    └── ...
```

## Prompt list and video IDs

Each line of `prompts/index_elements_prompts.txt` has three tab-separated columns:

```
<video_id>	<cultural_element>	<prompt>
010104	baozi	A man opens a bamboo steamer filled with hot baozi at a street food stall in China.
```

Lines that are empty or start with `#` are ignored.

A `video_id` is a 6-digit string `CCAAEE`:

| Digits | Meaning | Values |
|---|---|---|
| `CC` | Country | see table below |
| `AA` | Cultural aspect | see table below |
| `EE` | Index of the prompt within that country and aspect | `01`, `02`, ... |

For example, `010104` = China (`01`), Food (`01`), 4th prompt (`04`, baozi). Some cultural elements have two prompts, which appear as two consecutive IDs with the same cultural element.

| `CC` | Country | `CC` | Country |
|---|---|---|---|
| 01 | China | 07 | Russia |
| 02 | Japan | 08 | Italy |
| 03 | India | 09 | Netherlands |
| 04 | Malaysia | 10 | United States |
| 05 | Nigeria | 11 | New Zealand |
| 06 | Ethiopia | 12 | Brazil |

| `AA` | Aspect | `AA` | Aspect |
|---|---|---|---|
| 01 | Food | 08 | Dancing |
| 02 | Clothing | 09 | Games |
| 03 | Architecture | 10 | Sports |
| 04 | Decorations | 11 | Concert |
| 05 | Greetings | 12 | Wedding |
| 06 | Dining | 13 | Funeral |
| 07 | Celebrations | 14 | Religious Activity |


## Preparing videos

Generate one video per prompt with each model you want to evaluate, and save it as

```
./video_generate/<generation_model>/<video_id>_<anything>.mp4
```


## Running the evaluation

```bash
pip install google-genai
export GEMINI_API_KEY=...

python evaluate_gemini.py
```

## Audio evaluation scope

Gemini scores 2b (Audio Cultural Alignment) for every video, but we only use the 2b score when both conditions hold:

- the generation model produces audio (`AUDIO_MODEL_NAMES` in the script: kling3.0, veo3.1, happyhorse1.0, ltx2.3), and
- the aspect of the prompt (digits 3–4 of the video ID) is audio-related (`AUDIO_ASPECT_CODES` in the script): 07 Celebrations, 08 Dancing, 11 Concert, 12 Wedding, 13 Funeral, 14 Religious Activity.

The script writes two result files:

| File | Content |
|---|---|
| `gemini_video_culture_eval_results_raw.jsonl` | Every record exactly as returned by Gemini, including 2b scores outside the audio scope, failed attempts (`error` field), and retries. Each record has an `audio_in_scope` field. |
| `gemini_video_culture_eval_results_audiofiltered.jsonl` | One record per successfully evaluated video, with the 2b score set to `"NA"` when `audio_in_scope` is `false`. **Use this file to compute scores.** |

The audio-filtered file is rebuilt from the raw file at the end of every run. All other criteria are kept unchanged for every video.

If you evaluate a new model that generates audio, add it to both `MODEL_DIRS` and `AUDIO_MODEL_NAMES` in the script.


## Citation

```bibtex
@inproceedings{han2026culturevidbench,
  title={CultureVidBench: Benchmarking Cultural Understanding in Text-to-Video Generation},
  author={Han, Xianjing and Su, Yuhan and Deng, Yang and Ma, Dong and Tay, Wee Peng and Zhu, Bin},
  booktitle={Proceedings of the Conference on Empirical Methods in Natural Language Processing},
  year={2026}
}
```
