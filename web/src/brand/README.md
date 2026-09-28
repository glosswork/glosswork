# Brand assets

The Glosswork mark: the interlinear gloss (a line of text and a shorter line written above
it). Construction, sizes, variants and the wordmark lockup are specified in
`docs/DESIGN.md` section 4. These files are the source of truth for the geometry.

- `mark-tile.svg`: blue tile, amber bars. Identical to `web/public/favicon.svg`.
- `mark-bare.svg`: bars only, `currentColor`, for inline use below 20px and monochrome print.
- `mark-mono.svg`: ink tile, white bars, for single-color contexts.

`index.html` links the favicon; React imports the tile for the sidebar. Do not redraw the bars
in a component; import the file.
