# 5-Shot Reel Builder v3

Adds:
- Fade up from black at the start
- Crossfade between each of the 5 shots
- Short fade out at the end
- Shot requirements list on the page

Run:

```bash
cd five_shot_reel_builder_v3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Music file goes here:

```text
music/Cake Crumbs.mp3
```
