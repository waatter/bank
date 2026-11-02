# TROPOS 3.2.9 技术文档 LaTeX 更新版

主文件main.tex，分节正文在sections/，项目素材在assets/，核验证据在evidence/。

## 编译
```sh
latexmk -xelatex -interaction=nonstopmode -halt-on-error main.tex
```

使用kit原版paper.cls的中文日常版（journal,wp,nowatermark,nobib,clean）。没有改动共享kit或paper.cls。恢复kit的原生wp蓝色楷体标题、锈红节号与页眉、陶土色短线、Erewhon西文及数学、蓝色题注与三线表。封面和标题页沿用makecover/makewptitle，替换为项目logo与比赛字段。无FDU徽标、无底纹。

A4，正文11pt，中文1.35倍行距，宋体正文与楷体标题。来源paper.cls内部按字体可用性回退。

## 素材
最新tropos-mark-ink.svg原件保持不变，rsvg转换PDF随包提供。brand-logotype.tex将原标记与TROPOS文字组合，brand-logotype.pdf已预编译，普通文档重编不依赖素材生成工具。水墨长卷原始素材也保留在assets/。

## 环境
XeLaTeX、latexmk、TeX Live中的ctex/newtx/erewhon/TikZ/tcolorbox等包；无需Biber和外网。首次编译由latexmk自动完成目录与交叉引用多轮更新。

22页PDF已逐页视觉检查。编译无缺字、无越界、无未解析引用。原版kit中文封面使用small caps时有楷体字体形状回退警告，不影响字形显示。所有测试为文档核验时快照，独立证据与并行CSS变化观察见evidence/。
