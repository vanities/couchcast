# Icon

`icon-deck.jpg` is the gpt-image-2 design deck the icon came from: three directions, of which
"Couch Signal" won. It is the name itself, keeps the brand violet, and still reads at 16 px.
`icon-512.png` is the master.

To regenerate (needs `OPENAI_API_KEY`):

```bash
python3 docs/design/gen_image.py docs/design/icon-prompt.txt /tmp/icon.png 1024x1024 high -
uv run --with pillow --with numpy python docs/design/icon_process.py /tmp/icon.png /tmp/icons
```

gpt-image-2 can't render transparency, so the prompt asks for pure white, and `icon_process.py`
recovers the alpha from each pixel's distance to white, sets the fill to the brand violet
`#6d4aff`, trims, pads, and exports 16 to 512 px plus a light/dark contact sheet. Copy
`icon-{16,32,48,96,128}.png` into `extension/icons/` and `icon-128.png` to `amo/`.
