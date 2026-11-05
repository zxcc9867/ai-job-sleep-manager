# App icon

The monitor identifies the PC, the crescent identifies sleep, and the teal light identifies observed activity. This is the app's own identity, separate from the AI provider marks in task rows.

Created with the built-in `image_gen` tool. The original generated PNG is preserved as `sleep_manager/assets/app-icon-source.png`. Only format conversion and proportional resizing were used for the app resources; transparency was preserved.

- `sleep_manager/assets/app-icon.png`: 256px PNG for window identity.
- `sleep_manager/assets/app-icon-header.png`: 48px PNG for the header.
- `sleep_manager/assets/app-icon.ico`: 16,20,24,32,40,48,64,128,256px Windows icon.
- `docs/images/app-icon.png`: README preview.

## Generation prompt

```text
Use case: logo-brand. Create ONE final Windows desktop app icon for AI Job Sleep Manager, an app that keeps a PC awake during AI work and lets it sleep or shut down afterward. A simple, instantly recognizable computer monitor outline enclosing a large crescent moon, with one small teal activity light integrated into the upper-right edge of the monitor. Bold geometric shapes, flat vector-like finish, crisp edges, generous negative space, excellent legibility when reduced to 16x16 and 32x32 pixels. A dark navy rounded-square tile matching the existing app (#101722) with a clean white monitor outline and a pale cyan/soft blue crescent (#9bbeff), a restrained teal status light. Large centered symbol filling the tile, balanced compact silhouette. Transparent outside the rounded-square tile. No text, no letters, no wordmark, no multiple variants, no mockup, no desktop scene, no gradients, no 3D, no fine details, no shadows outside the tile, no watermark. Square high-resolution production icon.
```
