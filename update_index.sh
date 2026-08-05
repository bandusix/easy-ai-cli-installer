#!/bin/bash
# Add version markers to tool cards
sed -i 's|<div class="tool-version">v2.1.221</div>|<div class="tool-version" data-version="claude">v2.1.221</div><!-- claude -->|' index.html
sed -i 's|<div class="tool-version">rust-v0.146.0</div>|<div class="tool-version" data-version="codex">rust-v0.146.0</div><!-- codex -->|' index.html
sed -i 's|<div class="tool-version">v0.53.1</div>|<div class="tool-version" data-version="gemini">v0.53.1</div><!-- gemini -->|' index.html
sed -i '0,/<div class="tool-version">v0.31.1<\/div>/s|<div class="tool-version">v0.31.1</div>|<div class="tool-version" data-version="kimi">v0.31.1</div><!-- kimi -->|' index.html
sed -i '0,/<div class="tool-version">v1.0.82<\/div>/s|<div class="tool-version">v1.0.82</div>|<div class="tool-version" data-version="lark">v1.0.82</div><!-- lark -->|' index.html

# Add data-i18n attributes to translatable elements (will do this manually in next step)
echo "Version markers added"
