# Extension icons

Drop these three files in this folder before `pnpm build`:

- `icon-16.png`  (toolbar)
- `icon-48.png`  (extension management page)
- `icon-128.png` (Web Store listing)

The manifest references each by exact filename. Simple brand-colored brain
on white, indigo `#6366f1` accent, exported as flat PNGs (no transparency
artifacts — Chrome scales them poorly at the 16×16 size).
