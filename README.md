# Silver-Screen

**A Reparodynamics Production · TGRM · RYE · MSIL**

Full-length AI movie studio built on the open science of **Reparodynamics** — self-repairing systems applied to cinema.

| Layer | Role |
| --- | --- |
| **Reparodynamics** | Science of energy-bounded, aligned self-repair (five-law system) |
| **TGRM** | *Targeted Gradient Repair Mechanism* — Detect → Minimal correction → Verify → Reinforce |
| **RYE** | *Repair Yield per Energy* — ΔR / E |
| **MSIL** | *Meta Stability Intelligence Layer* — stability, act balance, collapse risk |
| **τ = 0.6** | Adaptive micro-repair threshold (micro vs full fix cost) |

Founder: **Cody Ryan Jenkins** ([@Reparodynamics](https://x.com/Reparodynamics)) · GitHub: [BoneManTGRM](https://github.com/BoneManTGRM)

---

## Quick start (Streamlit — primary)

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open the local URL Streamlit prints (usually http://localhost:8501).

### What you can do

1. **Brief** — premise, genre, tone, **format** (trailer → 90m feature)
2. **Your Images & Voices** — upload character portraits and voice samples
3. **Run TGRM Pipeline** — multi-act screenplay with fracture detection + scar memory, then chapter frames/reels using *your* media
4. **NFT metadata** — OpenSea-style attributes for RYE / MSIL / TGRM

Full-length formats render **chapter by chapter** so a 90-minute project stays practical.

---

## Science references

- TGRM: [10.5281/zenodo.17273433](https://doi.org/10.5281/zenodo.17273433)
- Five-law system: [10.5281/zenodo.17538091](https://doi.org/10.5281/zenodo.17538091)
- Coding velocity sim: [10.5281/zenodo.17336075](https://doi.org/10.5281/zenodo.17336075)
- Corpus: https://bonemantgrm.github.io/reparodynamics-corpus/
- Related engines: [Autonomous-research-agent](https://github.com/BoneManTGRM/Autonomous-research-agent), [Reparodynamics-TGRM-Automation](https://github.com/BoneManTGRM/Reparodynamics-TGRM-Automation)

### TGRM narrative loop

```text
DETECT     → plot holes, character drift, timeline breaks, theme noise, act imbalance
MINIMAL    → change one beat / line / plant (energy 1 if severity < τ, else 5)
VERIFY     → ΔR on continuity score; residual high-severity fractures
REINFORCE  → scar memory for winning fixes (reapplied on future projects)
MSIL       → stability index, collapse risk, verdict (stable | repairing | unstable)
RYE        → total ΔR / total energy
```

---

## Project layout

```text
streamlit_app.py          # primary UI
silver_screen/
  science.py              # τ, RYE, MSIL, five laws, formats
  tgrm.py                 # Detect → Minimal → Verify → Reinforce
  script_engine.py        # multi-act / multi-chapter screenplay
  media.py                # your images + voices → frames / chapter reels
  pipeline.py             # end-to-end runner
requirements.txt
src/                      # optional TanStack web studio (secondary)
archive/legacy-gradio/    # archived Gradio stubs
```

### Optional web studio

```bash
npm install
npm run dev      # http://localhost:8080
```

---

## License

Project code for Silver-Screen as published in this repository.  
Reparodynamics theory remains attributed to Cody Ryan Jenkins / BoneManTGRM.
