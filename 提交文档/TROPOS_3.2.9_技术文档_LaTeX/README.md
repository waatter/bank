# TROPOS 3.2.9 技术文档 LaTeX 更新版

主文件main.tex，正文在sections/，项目素材在assets/。

## 编译
```sh
latexmk -xelatex -interaction=nonstopmode -halt-on-error main.tex
```
使用kit的paper.cls中文日常版；未改变共享kit或paper.cls。nowatermark取消底纹，自定义封面使用项目最新水墨logo，无FDU标志。正文中文宋体、标题楷体，A4，11pt，1.35倍行距。PDF书签和目录由LaTeX生成。

环境：TeX Live及ctex/newtx/erewhon/TikZ等依赖。无需Biber或外网。字体按paper.cls规则回退。证据见evidence/。
