%%
%% paper.cls —— 科研工作流 · 论文/计划书/工作论文统一文档类（中英文同一个类）
%%
%% 编译：XeLaTeX + Biber（中英文都是这一条链；latexmk 读版本目录的 .latexmkrc 自动完成）
%%
%%   \documentclass{paper}               中文稿（默认；图表按节编号 图 2-1）
%%   \documentclass[journal]{paper}      中文稿·期刊观感（图表连续编号 图 1、题注标签加粗）
%%   \documentclass[en]{paper}           英文稿（图表连续编号 Figure 1.）
%%   \documentclass[mono]{paper}         黑白稿：取消所有链接配色（送审、打印）
%%   \documentclass[nowatermark]{paper}  不铺复旦校徽底纹（投稿匿名版常用）
%%   \documentclass[nobib]{paper}        类不代为加载 biblatex（要自己接管参考文献时）
%%   \documentclass[clean]{paper}        修订标记清稿：\revadd 只留文字，\revdel、\authornote 消失
%%   \documentclass[en,wp]{paper}        日常英文模板（期刊投稿仍用 [en]）：Letter、Erewhon、蓝色标题、要点框、
%%                                       封面同样有徽标与姓名/单位/课程/日期，另有标题页
%%                                       \makewptitle（题名、作者、摘要）；底纹照常
%%   \documentclass[journal,wp]{paper}   中文稿套 wp 版式：中文字体沿用中文模板（宋体正文、楷体标题与封面），
%%                                       西文与数学 Erewhon，配色、标题、题注、页眉照 wp；A4、行距放宽
%%
%% 中英文共用的东西（一处定义，两种语言同时生效）：
%%   · 封面：复旦徽标 + 文稿类型 + 题目 + 姓名/单位/课程/日期（\makecover）
%%   · 底纹：复旦校徽 background.pdf，封面以外每页都铺
%%   · 文内引用：整条引用（作者、年份、括号）统一为复旦蓝 papercite
%%   · 特殊词汇：\term{...} 一律斜体；中文稿里的英文术语同样斜体
%%   · 参考文献：中文稿 = 《世界经济》体例（paper-sjjj）；英文稿 = AER 体例（paper-aer）
%%
%% 规则书：~/research/latex/README.md。项目里的副本（paper/_latex/）由
%% ~/research/latex/tools/sync_latex.sh 同步，**不要在项目里改**——改底座，再同步。
%%
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{paper}[2026/10/02 v2.2 科研工作流统一论文类（中英文同类）]

%% ============================================================
%% 0. 选项
%% ============================================================
\newif\ifpaper@en        \paper@enfalse
\newif\ifpaper@journal   \paper@journalfalse
\newif\ifpaper@mono      \paper@monofalse
\newif\ifpaper@wm        \paper@wmtrue
\newif\ifpaper@bib       \paper@bibtrue
\newif\ifpaper@clean     \paper@cleanfalse
\newif\ifpaper@wp        \paper@wpfalse
\DeclareOption{en}{\paper@entrue}
\DeclareOption{wp}{\paper@wptrue}
\DeclareOption{zh}{\paper@enfalse}
\DeclareOption{journal}{\paper@journaltrue}
\DeclareOption{mono}{\paper@monotrue}
\DeclareOption{nowatermark}{\paper@wmfalse}
\DeclareOption{nobib}{\paper@bibfalse}
\DeclareOption{clean}{\paper@cleantrue}
\DeclareOption*{\PassOptionsToClass{\CurrentOption}{ctexart}}
\ProcessOptions\relax

%% 英文稿：ctex 的 plain 方案保留英文名（Contents / Figure / Table / References），
%% 图表一律连续编号（英文稿不用"图 2-1"式按节编号）。
\ifpaper@en
  \PassOptionsToClass{scheme=plain}{ctexart}
  \paper@journaltrue
\fi

%% ============================================================
%% 1. 基础类（fontset=none：字体由 §4 显式接管，不让 ctex 猜系统字体）
%% ============================================================
%% wp：英文 Letter、中文 A4，11pt；数学字体交给 newtxmath（§4.1），fontspec 不接管数学
\ifpaper@wp
  \PassOptionsToPackage{no-math}{fontspec}
  \ifpaper@en
    \LoadClass[letterpaper, 11pt, fontset=none]{ctexart}
  \else
    \LoadClass[a4paper, 11pt, fontset=none]{ctexart}
  \fi
\else
  \LoadClass[a4paper, 12pt, fontset=none]{ctexart}
\fi

%% ============================================================
%% 2. 宏包
%% ============================================================
\RequirePackage{geometry}
\RequirePackage{setspace}
\RequirePackage{amsmath}
\ifpaper@wp\else
  \RequirePackage{amssymb, amsfonts}   %% wp 由 newtxmath 提供这些符号，同时加载会重复定义
\fi
\RequirePackage{amsthm}
\RequirePackage{graphicx}
\RequirePackage{float}
\RequirePackage{fancyhdr}
\RequirePackage{titlesec}
\RequirePackage{tocloft}
\RequirePackage{booktabs}
\RequirePackage{tabularx}
\RequirePackage{multirow}
\RequirePackage{caption}
\RequirePackage{subcaption}
\RequirePackage{anyfontsize}
\RequirePackage{xcolor}
\RequirePackage{csquotes}
\RequirePackage[hidelinks]{hyperref}   %% 加载时保持 hidelinks（兼容旧稿的同名声明），
                                       %% 颜色在 §2.5 于 \begin{document} 处统一覆盖
\RequirePackage{indentfirst}
\RequirePackage{enumitem}
\RequirePackage{eso-pic}
\RequirePackage{newunicodechar}
\ifpaper@wp
  \RequirePackage{colortbl}             %% 表线着色（\arrayrulecolor）
  \RequirePackage{tcolorbox}            %% 要点框 keybox 与命题框
  \tcbuselibrary{skins, breakable}
\fi

%% ============================================================
%% 2.5 配色（改色的唯一入口）
%% ============================================================
%% papercite：文内引用。取复旦校徽标准蓝（logo.png 实测 RGB 8,48,144 = #083090），
%%            与封面徽标同色；深于常见网页链接蓝，印成灰度也比正文浅一档、仍可分辨。
%% paperlink：图/表/式/节的交叉引用（深红，与"出处"的蓝区分开：蓝=有出处，红=在本文哪里）。
%% paperurl ：网址、DOI。
\definecolor{papercite}{RGB}{8,48,144}
\definecolor{paperlink}{RGB}{164,42,42}
\definecolor{paperurl} {RGB}{0,110,110}

%% wp 版式色板（2026-10-17 从参照工作论文 PDF 取样）：
%% wpblue 题目、标题、表题与表线；wprust 页眉与节号；wpclay 题下短线与框的左竖条；
%% wpcream 框底色。wp 下三种链接色也换成同一色系：引用中蓝、交叉引用深蓝、网址锈红。
\definecolor{wpblue} {RGB}{42,84,144}
\definecolor{wprust} {RGB}{168,91,68}
\definecolor{wpclay} {RGB}{204,120,92}
\definecolor{wpcream}{RGB}{245,244,237}
\ifpaper@wp
  \definecolor{papercite}{RGB}{50,102,173}
  \definecolor{paperlink}{RGB}{31,63,110}
  \definecolor{paperurl} {RGB}{168,91,68}
\fi

\ifpaper@mono
  \def\paper@citecolor{}%
\else
  \def\paper@citecolor{\color{papercite}}%
  %% 必须在导言区设（hyperref 在 \begin{document} 之后不再接受 colorlinks）
  \hypersetup{colorlinks=true, linkcolor=paperlink, citecolor=papercite,
              urlcolor=paperurl, filecolor=paperurl, pdfborder={0 0 0}}%
\fi

%% ============================================================
%% 3. 版心：A4，上下 2.54cm，左右 3cm（中英文相同）
%% ============================================================
\ifpaper@wp
  %% wp：左右 1.25in（英文 Letter 版心宽 6in，与参照 PDF 实测一致；中文 A4 同边距）
  \geometry{top=1.2in, bottom=1.15in, left=1.25in, right=1.25in,
            headheight=12pt, headsep=0.32in, footskip=0.5in}
\else
  \geometry{top=2.54cm, bottom=2.54cm, left=3cm, right=3cm}
\fi

%% ============================================================
%% 4. 字体（逐级回退：缺字体只会降档，绝不编译失败）
%% ============================================================
%% 4.1 西文：Times New Roman → TeX Gyre Termes
%%     wp：正文 Erewhon（Utopia 衍生，TeX Live 自带 erewhon 包），数学 newtxmath 的 erewhon 字形。
%%     newtxmath 须在 \setmainfont 之前；XeLaTeX 下其 operators 默认指向 TU 编码里不存在的
%%     OT1 字族，数字与 \ln 会落到 Latin Modern，故显式指向 erewhon 的 Type 1 字族。
\ifpaper@wp
  \IfFileExists{erewhon.sty}{%
    \RequirePackage[erewhon, vvarbb]{newtxmath}%
    \DeclareSymbolFont{operators}{OT1}{erewhon-LF}{m}{n}%
    \SetSymbolFont{operators}{bold}{OT1}{erewhon-LF}{b}{n}%
    \DeclareMathAlphabet{\mathrm}{OT1}{erewhon-LF}{m}{n}%
    \DeclareMathAlphabet{\mathbf}{OT1}{erewhon-LF}{b}{n}%
    \DeclareMathAlphabet{\mathit}{OT1}{erewhon-LF}{m}{it}%
    \setmainfont{Erewhon}[Extension=.otf, UprightFont=*-Regular, BoldFont=*-Bold,
      ItalicFont=*-Italic, BoldItalicFont=*-BoldItalic, SlantedFont=*-RegularSlanted]%
  }{%
    \ClassWarning{paper}{wp: 未找到 erewhon 字体包，退回 TeX Gyre Termes + newtxmath}%
    \RequirePackage{newtxmath}%
    \setmainfont{TeX Gyre Termes}%
  }
\else
\IfFontExistsTF{Times New Roman}
  {\setmainfont{Times New Roman}}
  {\setmainfont{TeX Gyre Termes}}
\fi

%% 4.2 中文正文：SimSun；粗体由宋体模拟，不切换成黑体或等线。
%%     macOS Office 字体目录 → 系统已安装的 SimSun → STSong → Songti SC → Noto Serif CJK → Fandol
\IfFileExists{/Applications/Microsoft Word.app/Contents/Resources/DFonts/Simsun.ttc}{%
  \setCJKmainfont{SimSun}[%
    Path={/Applications/Microsoft Word.app/Contents/Resources/DFonts/},%
    Extension=.ttc,FontIndex=0,%
    BoldFont=*,ItalicFont=*,BoldItalicFont=*,%
    BoldFeatures={FakeBold=4},BoldItalicFeatures={FakeBold=4}]%
}{\IfFontExistsTF{SimSun}{%
  \setCJKmainfont{SimSun}[AutoFakeBold=true]%
}{\IfFontExistsTF{STSong}{%
  \setCJKmainfont{STSong}[AutoFakeBold=true]%
}{\IfFontExistsTF{Songti SC}{%
  \setCJKmainfont{Songti SC}[AutoFakeBold=true]%
}{\IfFontExistsTF{Noto Serif CJK SC}{%
  \setCJKmainfont{Noto Serif CJK SC}[AutoFakeBold=true]%
}{%
  \setCJKmainfont{FandolSong-Regular}[AutoFakeBold=true]%
}}}}}

%% 4.3 中文标题：优先 Office 本地楷体，避免 macOS 对缺字族探测触发下载等待；再逐级回退
\IfFileExists{/Applications/Microsoft Word.app/Contents/Resources/DFonts/Kaiti.ttf}{%
  \newCJKfontfamily\paperkaiti{Kaiti.ttf}[%
    Path={/Applications/Microsoft Word.app/Contents/Resources/DFonts/},%
    BoldFont=*,ItalicFont=*,BoldItalicFont=*,%
    BoldFeatures={FakeBold=4},BoldItalicFeatures={FakeBold=4}]%
}{\IfFontExistsTF{STKaiti}{%
  \newCJKfontfamily\paperkaiti{STKaiti}[AutoFakeBold=true]%
}{\IfFontExistsTF{Kaiti SC}{%
  \newCJKfontfamily\paperkaiti{Kaiti SC}[AutoFakeBold=true]%
}{\IfFontExistsTF{KaiTi}{%
  \newCJKfontfamily\paperkaiti{KaiTi}[AutoFakeBold=true]%
}{\IfFontExistsTF{FandolKai-Regular}{%
  \newCJKfontfamily\paperkaiti{FandolKai-Regular}[AutoFakeBold=true]%
}{%
  \newCJKfontfamily\paperkaiti{Noto Serif CJK SC}[AutoFakeBold=true]%
}}}}}
\let\kaiti\paperkaiti   %% 兼容旧稿里的 \kaiti

%% 4.4 中文无衬线：华文黑体 → 黑体 → Noto Sans CJK
\IfFileExists{/System/Library/Fonts/STHeiti Medium.ttc}{%
  \setCJKsansfont{STHeiti Medium.ttc}[%
    Path={/System/Library/Fonts/},FontIndex=0,%
    BoldFont=*,ItalicFont=*,BoldItalicFont=*]%
}{\IfFontExistsTF{STHeiti}{\setCJKsansfont{STHeiti}}{%
  \IfFontExistsTF{SimHei}{\setCJKsansfont{SimHei}}{%
    \IfFontExistsTF{Noto Sans CJK SC}{\setCJKsansfont{Noto Sans CJK SC}}{}}}}

%% 4.4b 中文等宽（代码、\texttt 里的汉字）：华文仿宋 → 仿宋 → Noto Sans CJK
\IfFileExists{/Applications/Microsoft Word.app/Contents/Resources/DFonts/Fangsong.ttf}{%
  \setCJKmonofont{Fangsong.ttf}[%
    Path={/Applications/Microsoft Word.app/Contents/Resources/DFonts/},%
    BoldFont=*,ItalicFont=*,BoldItalicFont=*]%
}{\IfFontExistsTF{STFangsong}{\setCJKmonofont{STFangsong}}{%
  \IfFontExistsTF{FangSong}{\setCJKmonofont{FangSong}}{%
    \IfFontExistsTF{Noto Sans CJK SC}{\setCJKmonofont{Noto Sans CJK SC}}{}}}}

%% 4.5 缺字回退：Times 无 ★ 等符号，会静默丢字；新符号照此补一条
\newunicodechar{★}{\ensuremath{\star}}
\newunicodechar{☆}{\ensuremath{\star}}

%% 4.6 英文稿的标点：xeCJK 默认把 “ ” ‘ ’ — … 当成中文全角标点，
%%     英文正文里直接写这些字符会被排成全角（前后空一大块）。英文稿里改判为西文。
\ifpaper@en
  \xeCJKDeclareCharClass{Default}{"2014, "2015, "2018, "2019, "201C, "201D, "2026}
\fi

%% 4.7 西文片段开关：在中文稿里排一段纯西文（英文文献条目、\term）时关闭 xeCJK 的
%%     字符分类，使其中的引号、撇号、破折号按西文字体排，不被当成全角标点。
\newcommand{\paperlatin}[1]{{\XeTeXinterchartokenstate=0 #1}}

%% ============================================================
%% 5. 正文：小四（12pt），1.5 倍行距
%% ============================================================
%% wp：11pt，行距 15.2pt（参照 PDF 实测）。不用 \zihao：\setstretch 会经 \@currsize
%% 把 \zihao 设的字号重置回 \normalsize，wp 一律用 LaTeX 标准字号命令并先 \setstretch。
%% 中文稿套 wp：同为 11pt，行距放宽到 1.35（中文按 15pt 行距排太挤）；
%% \paper@wpcjk 让 wp 的标题、页眉、封面在中文稿里用楷体（只影响汉字，西文仍是 Erewhon）。
\ifpaper@en
  \newcommand{\paper@wpstretch}{1.12}
  \newcommand{\paper@wpcjk}{}
\else
  \newcommand{\paper@wpstretch}{1.35}
  \newcommand{\paper@wpcjk}{\paperkaiti}
\fi
\ifpaper@wp
  \AtBeginDocument{\setstretch{\paper@wpstretch}\normalsize}
\else
  \AtBeginDocument{\zihao{-4}\setstretch{1.5}}
\fi
\ifpaper@wp\ifpaper@en
  \setlength{\parindent}{1.3em}
\else
  \setlength{\parindent}{2em}
\fi\else\ifpaper@en
  \setlength{\parindent}{1.5em}
\else
  \setlength{\parindent}{2em}
\fi\fi
\setlength{\parskip}{0pt}
\ifpaper@en\else\ifpaper@wp
  %% 中文稿套 wp：与英文 wp 一致的悬挂缩进——编号在左、换行与首行文字对齐
  \setlist[itemize,enumerate]{leftmargin=2em,labelindent=0pt,itemindent=0pt,
    labelsep=0.5em,itemsep=0.25em,topsep=0.5em,parsep=0pt}
\else
  %% 列表项首行缩进；换行回正文左边界，不做悬挂缩进。
  \setlist[itemize,enumerate]{leftmargin=0pt,labelindent=0pt,itemindent=2.5em,
    labelwidth=2em,labelsep=0.5em,itemsep=0pt,topsep=0.35em,parsep=0pt}
\fi\fi

%% ============================================================
%% 6. 标题
%% ============================================================
\ifpaper@wp
  %% 节号锈红、节名蓝；小节与小小节全蓝；\paragraph 为行内蓝色粗体引导语（自带句点：\paragraph{Outline.}）
  %% 中文稿：汉字用楷体（\paper@wpcjk），编号到 subsubsection
  \ifpaper@en\else\setcounter{secnumdepth}{3}\fi
  \titleformat{\section}{\Large\bfseries\paper@wpcjk\color{wpblue}}{\textcolor{wprust}{\thesection.}}{0.9em}{}
  \titlespacing*{\section}{0pt}{1.9em plus 0.4em}{0.9em}
  \titleformat{\subsection}[block]{\large\bfseries\paper@wpcjk\color{wpblue}}{\thesubsection}{0.9em}{}
  \titlespacing*{\subsection}{0pt}{1.4em plus 0.3em}{0.6em}
  \titleformat{\subsubsection}[block]{\normalsize\bfseries\paper@wpcjk\color{wpblue}}{\thesubsubsection}{0.9em}{}
  \titlespacing*{\subsubsection}{0pt}{1.2em plus 0.3em}{0.5em}
  \titleformat{\paragraph}[runin]{\normalsize\bfseries\paper@wpcjk\color{wpblue}}{}{0pt}{}
  \titlespacing*{\paragraph}{0pt}{1.1em plus 0.3em}{0.9em}
\else\ifpaper@en
  \titleformat{\section}{\zihao{-3}\bfseries}{\thesection}{0.6em}{}
  \titlespacing*{\section}{0pt}{1.2em}{0.8em}
  \titleformat{\subsection}[block]{\zihao{4}\bfseries}{\thesubsection}{0.6em}{}
  \titlespacing*{\subsection}{0pt}{0.8em}{0.5em}
  \titleformat{\subsubsection}[block]{\zihao{-4}\bfseries\itshape}{\thesubsubsection}{0.6em}{}
  \titlespacing*{\subsubsection}{0pt}{0.6em}{0.4em}
\else
  \setcounter{secnumdepth}{3}
  \setcounter{tocdepth}{2}  %% 编号到 subsubsection；目录只收录到 subsection
  \titleformat{\section}{\zihao{-3}\bfseries\paperkaiti\centering}{\thesection}{0.5em}{}
  \titlespacing*{\section}{0pt}{1.2em}{0.8em}
  \titleformat{\subsection}[block]{\zihao{4}\bfseries\paperkaiti}{\hspace{2em}\thesubsection}{0.5em}{}
  \titlespacing*{\subsection}{0pt}{0.8em}{0.5em}
  \titleformat{\subsubsection}[block]{\zihao{-4}\bfseries\paperkaiti}{\hspace{2em}\thesubsubsection}{0.5em}{}
  \titlespacing*{\subsubsection}{0pt}{0.6em}{0.4em}
  %% 更深层级标题也显式指定宋/楷字体，不继承默认黑体或无衬线。
  \titleformat{\paragraph}[block]{\zihao{-4}\bfseries\paperkaiti}{\theparagraph}{0.5em}{}
  \titlespacing*{\paragraph}{0pt}{0.5em}{0.2em}
  \titleformat{\subparagraph}[block]{\zihao{-4}\bfseries\paperkaiti}{\thesubparagraph}{0.5em}{}
  \titlespacing*{\subparagraph}{0pt}{0.4em}{0.2em}
\fi\fi

%% ============================================================
%% 7. 目录（目录里的条目一律黑色，不套交叉引用红）
%% ============================================================
\ifpaper@wp
  %% 目录：标题蓝；节条目粗体黑；小节条目蓝（与参照 PDF 一致）。条目文字是超链接，
  %% 链接色会压过字体色，所以按层级改 hyperref 的 \@linkcolor，而不是 \color。
  \renewcommand{\cfttoctitlefont}{\Large\bfseries\paper@wpcjk\color{wpblue}}
  \renewcommand{\cftsecfont}{\bfseries\paper@wpcjk\def\@linkcolor{black}}
  \renewcommand{\cftsecpagefont}{\bfseries}
  \renewcommand{\cftsubsecfont}{\paper@wpcjk\def\@linkcolor{wpblue}}
  \renewcommand{\cftsubsubsecfont}{\paper@wpcjk\def\@linkcolor{wpblue}}
\else\ifpaper@en
  \renewcommand{\cfttoctitlefont}{\zihao{3}\bfseries}
  \renewcommand{\cftsecfont}{\zihao{-4}}
  \renewcommand{\cftsubsecfont}{\zihao{5}}
  \renewcommand{\cftsubsubsecfont}{\zihao{5}}
\else
  \renewcommand{\contentsname}{\paperkaiti 目\quad 录}
  \renewcommand{\cfttoctitlefont}{\zihao{3}\bfseries\paperkaiti}
  \renewcommand{\cftaftertoctitle}{\par}
  \renewcommand{\cftsecfont}{\zihao{-4}\paperkaiti}
  \renewcommand{\cftsubsecfont}{\zihao{5}\paperkaiti}
  \renewcommand{\cftsubsubsecfont}{\zihao{5}\paperkaiti}
  \renewcommand{\cftsecpagefont}{\paperkaiti}
  \renewcommand{\cftsubsecpagefont}{\paperkaiti}
  \renewcommand{\cftsubsubsecpagefont}{\paperkaiti}
\fi\fi
\setlength{\cftbeforesecskip}{0.7em}
\setlength{\cftbeforesubsecskip}{0.3em}
\setlength{\cftbeforetoctitleskip}{0pt}
\setlength{\cftaftertoctitleskip}{1.8em}

%% ============================================================
%% 8. 页眉页脚：左＝文稿类型＋标题，右＝当前节，页脚居中页码
%% ============================================================
\pagestyle{fancy}
\fancyhf{}
\ifpaper@wp
  %% wp：页眉居中一行锈红小型大写短题名（\wpshorttitle，缺省取主标题），无页眉线；页码同正文字号
  \fancyhead[C]{\footnotesize\paper@wpcjk\color{wprust}\scshape\MakeLowercase{\paper@runninghead}}
  \fancyfoot[C]{\thepage}
  \renewcommand{\headrulewidth}{0pt}
  \fancypagestyle{plain}{\fancyhf{}\fancyfoot[C]{\thepage}\renewcommand{\headrulewidth}{0pt}}
\else\ifpaper@en
  \fancyhead[L]{\zihao{-5}\itshape\@doctype\enspace\@assigntitle}
  \fancyhead[R]{\zihao{-5}\itshape\leftmark}
\else
  \fancyhead[L]{\zihao{-5}\paperkaiti\@doctype\quad\@assigntitle\@assignsubtitle}
  \fancyhead[R]{\zihao{-5}\paperkaiti\leftmark}
\fi
\fancyfoot[C]{\zihao{-5}\thepage}
\renewcommand{\headrulewidth}{0.5pt}
\fi
\renewcommand{\sectionmark}[1]{\markboth{\thesection\quad #1}{}}

%% ============================================================
%% 9. 图表与公式编号
%% ============================================================
\ifpaper@journal
  \renewcommand{\theequation}{\arabic{equation}}
  \renewcommand{\thefigure}{\arabic{figure}}
  \renewcommand{\thetable}{\arabic{table}}
\else
  \numberwithin{equation}{section}
  \numberwithin{figure}{section}
  \numberwithin{table}{section}
  \renewcommand{\theequation}{\thesection-\arabic{equation}}
  \renewcommand{\thefigure}{\thesection-\arabic{figure}}
  \renewcommand{\thetable}{\thesection-\arabic{table}}
\fi

%% ============================================================
%% 10. 题注：表题在上、图题在下，小五号
%% ============================================================
\DeclareCaptionFont{xiaowu}{\zihao{-5}}
\ifpaper@en
  \captionsetup{font=xiaowu, labelfont=bf, labelsep=period}
\else
  \ifpaper@journal
    \captionsetup{font=xiaowu, labelsep=quad, labelfont=bf}
  \else
    \captionsetup{font=xiaowu, labelsep=quad}
  \fi
\fi
\captionsetup[table]{position=above, aboveskip=4pt, belowskip=2pt}
\captionsetup[figure]{position=below, aboveskip=4pt, belowskip=2pt, justification=centering}
\ifpaper@wp
  %% wp：图题、表题都在上方，蓝色、居中、"Table 1:" 标签不加粗；表内小一号，表线蓝色。
  %% 图题在上方须把 \caption 写在 \includegraphics 之前。只挂 table 不挂 tabular（标题页里的
  %% tabular 不应被缩小）；先 \setstretch 后改字号，见 §5。
  \DeclareCaptionFont{wpblue}{\color{wpblue}}
  \captionsetup{font=wpblue, labelfont=wpblue, justification=centering}
  \ifpaper@en\captionsetup{labelsep=colon}\else\captionsetup{labelsep=quad}\fi   %% 中文：表 1　题名
  \captionsetup[table]{position=above, skip=9pt}
  \captionsetup[figure]{position=above, skip=9pt}
  \arrayrulecolor{wpblue}
  \urlstyle{same}   %% 网址用正文字体（锈红色来自 paperurl），不切等宽
\else
\AtBeginEnvironment{table}{\zihao{-5}\setstretch{1.0}}
\AtBeginEnvironment{tabular}{\zihao{-5}\setstretch{1.0}}
\fi
%% 浮动体内的设置挂在 \@floatboxreset 上：\AtBeginEnvironment{figure} 在浮动盒建立之前执行，
%% 随后 \@parboxrestore 会清掉 \leftskip，\centering 与字号设置因此从未生效（图一直按段落缩进
%% 左排）。\@floatboxreset 在盒内执行，\@captype 此时已是 figure / table。
\newcommand{\paper@tabletype}{table}
\newcommand{\paper@floatsetup}{%
  \centering
  \ifpaper@wp\ifx\@captype\paper@tabletype\setstretch{1.0}\small\fi\fi}
\g@addto@macro\@floatboxreset{\paper@floatsetup}

%% ============================================================
%% 10.5 表格：全宽三线表 threelinetab（宽度恒等于 \textwidth）
%% ============================================================
\ifpaper@wp
  \setlength{\heavyrulewidth}{0.8pt}
  \setlength{\lightrulewidth}{0.4pt}
  \setlength{\aboverulesep}{0.5ex}
  \setlength{\belowrulesep}{0.6ex}
\else
\setlength{\heavyrulewidth}{1.0pt}
\setlength{\lightrulewidth}{0.5pt}
\setlength{\aboverulesep}{0.35ex}
\setlength{\belowrulesep}{0.45ex}
\fi
\newcommand{\paper@tabfont}{\ifpaper@wp\setstretch{1.0}\small\else\zihao{-5}\setstretch{1.0}\fi}
\newenvironment{threelinetab}[1]{%
  \begingroup
  \paper@tabfont
  \let\hline\midrule   %% 脚本导出的 fragment 带 \hline：在此改指 \midrule，不必手改 fragment
  \setlength{\tabcolsep}{4pt}%
  \begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}#1@{}}%
}{%
  \end{tabular*}\endgroup
}

%% 图注 / 表注：不写进 \caption，题注之后独立一段（小五、悬挂缩进）
\newcommand{\paper@notelabel}{\ifpaper@en Note: \else 注：\fi}
\newcommand{\paper@floatnote}[1]{%
  \par\vspace{0.35em}%
  {\ifpaper@wp\setstretch{\ifpaper@en 1.1\else 1.25\fi}\small\else\zihao{-5}\setstretch{1.15}\fi\setlength{\parindent}{0pt}%
   \hangindent=2em\hangafter=1
   \noindent\paper@notelabel#1\par}}
\newcommand{\tablenote}[1]{\paper@floatnote{#1}}
\newcommand{\fignote}[1]{\paper@floatnote{#1}}
\let\tabnote\tablenote   %% 兼容旧英文套件 econpaper.sty 的写法

\newcommand{\vn}[1]{\textit{#1}}                  %% 表内变量名
\newcommand{\tabgroup}[2]{\multicolumn{#1}{c}{#2}} %% 跨列表头
%% esttab fragment 里的显著性星号 \sym{**}
\providecommand{\sym}[1]{\ifmmode^{#1}\else\(^{#1}\)\fi}

%% 缺件不炸（骨架阶段）：图、表 fragment 还没生成时画占位框，而不是编译失败。
%% 管线稳定后可换回裸 \includegraphics / \input，让"产物缺失"当场暴露。
\newcommand{\paper@missing}{\ifpaper@en missing: \else 缺件：\fi}
\newcommand{\figinclude}[2][\textwidth]{%
  \IfFileExists{#2}{\includegraphics[width=#1]{#2}}%
    {\fbox{\parbox[c][2.5cm][c]{0.9\textwidth}{\centering\ttfamily\footnotesize
      \paper@missing\detokenize{#2}}}}}
\newcommand{\fraginput}[1]{%
  \IfFileExists{#1}{\input{#1}}%
    {\textit{\footnotesize\paper@missing\detokenize{#1}}\\}}

%% ============================================================
%% 10.7 附录：\begin{paperappendix} … \end{paperappendix}
%%      附录节 = A/B/C；表 = A1；图 = A1；式 = (A.1)
%%      label 前缀：正文 fig: tab: s:，附录 afig: atab: as:
%% ============================================================
\newenvironment{paperappendix}{%
  \clearpage
  \appendix
  \numberwithin{equation}{section}%
  \renewcommand{\theequation}{\thesection.\arabic{equation}}%
  \setcounter{table}{0}\renewcommand{\thetable}{A\arabic{table}}%
  \setcounter{figure}{0}\renewcommand{\thefigure}{A\arabic{figure}}%
}{}

%% ============================================================
%% 10.8 特殊词汇斜体
%% ============================================================
%% \term{commitment problem}            → 斜体（英文稿、中文稿都用它）
%% \zhen{承诺问题}{commitment problem}   → 中文稿：承诺问题（commitment problem，斜体）
%%                                        英文稿：commitment problem（斜体）
%% 规则：概念性专门术语、外来语、首次引入的英文术语用 \term；
%%       普通英文专名（机构、数据库、期刊名、人名）不斜体。
\ifpaper@en
  \newcommand{\term}[1]{\textit{#1}}
  \newcommand{\zhen}[2]{\term{#2}}
\else
  \newcommand{\term}[1]{\paperlatin{\textit{#1}}}
  \newcommand{\zhen}[2]{#1（\term{#2}）}
\fi

%% ============================================================
%% 10.85 修订标记（R&R 之后的 markup 稿）
%% ============================================================
%% 改动直接写在正文里，同一份源文件出两个版本：
%%   \revadd{新增的文字}      markup 版：绿色下划线；clean 版：原样保留
%%   \revdel{删掉的文字}      markup 版：红色删除线；clean 版：消失
%%   \authornote{给合作者的话} markup 版：橙色方括号；clean 版：消失并在日志里警告（提交前清零）
%% 出清稿：\documentclass[...,clean]{paper}，或 make_submit.py --clean。
%% 首选 xeCJKfntef（\CJKunderline / \CJKsout：中英文混排都能正确断行；它依赖 l3draw）；
%% 缺件时退回 ulem（西文正常，长段中文可能不断行）。
\def\paper@fntef{ulem}
\IfFileExists{l3draw.sty}{\IfFileExists{accsupp.sty}{\IfFileExists{xeCJKfntef.sty}
  {\def\paper@fntef{cjk}}{}}{}}{}
\def\paper@tmp{cjk}
\ifx\paper@fntef\paper@tmp
  \RequirePackage{xeCJKfntef}
  \let\paper@uline\CJKunderline
  \let\paper@sout\CJKsout
\else
  \RequirePackage[normalem]{ulem}
  \let\paper@uline\uline
  \let\paper@sout\sout
\fi
\definecolor{paperrevadd}{RGB}{0,120,60}
\definecolor{paperrevdel}{RGB}{190,30,30}
\definecolor{papernote}{RGB}{210,110,0}
\ifpaper@clean
  \newcommand{\revadd}[1]{#1}
  \newcommand{\revdel}[1]{}
  \newcommand{\authornote}[1]{\ClassWarning{paper}{残留的 \string\authornote：#1}}
\else
  \newcommand{\revadd}[1]{{\color{paperrevadd}\paper@uline{#1}}}
  \newcommand{\revdel}[1]{{\color{paperrevdel}\paper@sout{#1}}}
  \newcommand{\authornote}[1]{{\color{papernote}\bfseries［#1］}}
\fi

%% ============================================================
%% 10.9 定理类环境（按语言命名；main.tex 已自行定义的同名环境优先）
%% ============================================================
%% 理论命题用楷体正文；整个命题块左边界一致，不悬挂。后续解释段落用 theorytext。
\newtheoremstyle{paperzh}{0.8em}{0.8em}{\paperkaiti}{2em}
  {\bfseries\paperkaiti}{：}{0.5em}{}
%% wp：命题标签蓝色粗体、说明蓝色常规体，正文直立；命题类放进与 keybox 同款的框
\newtheoremstyle{paperwp}{0pt}{0pt}{\normalfont}{0pt}
  {\bfseries\color{wpblue}}{.}{0.6em}
  {\thmname{#1}\thmnumber{ #2}\thmnote{ \mdseries(#3)}}
\newcommand{\paper@thm}[3][plain]{%
  \@ifundefined{#2}{\theoremstyle{#1}\newtheorem{#2}{#3}}{}}
\newcommand{\paper@boxthm}[1]{%
  \@ifundefined{#1}{}{\tcolorboxenvironment{#1}{paper@wpbox}}}
\AtEndPreamble{%
  \ifpaper@wp\ifpaper@en\def\paper@tmp{wpen}\else\def\paper@tmp{wpzh}\fi\else\def\paper@tmp{}\fi
  \def\paper@tmpb{wpen}%
  \ifx\paper@tmp\paper@tmpb
    \paper@thm[paperwp]{proposition}{Proposition}\paper@thm[paperwp]{lemma}{Lemma}%
    \paper@thm[paperwp]{theorem}{Theorem}\paper@thm[paperwp]{corollary}{Corollary}%
    \paper@thm[paperwp]{hypothesis}{Hypothesis}%
    \paper@thm[paperwp]{assumption}{Assumption}\paper@thm[paperwp]{definition}{Definition}%
    \paper@boxthm{proposition}\paper@boxthm{lemma}\paper@boxthm{theorem}%
    \paper@boxthm{corollary}%
  \else\ifpaper@en
    \paper@thm{proposition}{Proposition}\paper@thm{lemma}{Lemma}%
    \paper@thm{theorem}{Theorem}\paper@thm{corollary}{Corollary}%
    \paper@thm{hypothesis}{Hypothesis}%
    \paper@thm[definition]{assumption}{Assumption}\paper@thm[definition]{definition}{Definition}%
  \else
    \theoremstyle{paperzh}%
    \paper@thm[paperzh]{proposition}{命题}\paper@thm[paperzh]{lemma}{引理}%
    \paper@thm[paperzh]{theorem}{定理}\paper@thm[paperzh]{corollary}{推论}%
    \paper@thm[paperzh]{hypothesis}{假说}%
    \paper@thm[paperzh]{assumption}{假设}\paper@thm[paperzh]{definition}{定义}%
    \ifpaper@wp   %% 中文稿套 wp：命题类装进同款框，标签与正文仍按中文模板（楷体）
      \paper@boxthm{proposition}\paper@boxthm{lemma}\paper@boxthm{theorem}%
      \paper@boxthm{corollary}%
    \fi
  \fi\fi
  \theoremstyle{plain}}

%% ============================================================
%% 10.95 要点框 keybox：\begin{keybox}[In a nutshell.] … \end{keybox}
%% ============================================================
%% 可选参数是蓝色粗体引导语（自带句点）；框内段落照常缩进，首段顶格；可跨页。
%% wp：米色底、左侧陶土色竖条；其他版式退化为左右缩进的普通段落块，同一份源文件可在 en 与 wp 间切换。
\ifpaper@wp
  \tcbset{paper@wpbox/.style={enhanced, breakable, sharp corners, boxrule=0pt,
    colback=wpcream, colframe=wpcream, frame hidden,
    borderline west={2.2pt}{0pt}{wpclay},
    left=12pt, right=12pt, top=7pt, bottom=7pt, before skip=10pt, after skip=10pt,
    parbox=false, before upper={\setlength{\parindent}{\ifpaper@en 1.3em\else 2em\fi}\noindent}}}
  \newtcolorbox{keybox}[1][]{paper@wpbox,
    before upper={\setlength{\parindent}{\ifpaper@en 1.3em\else 2em\fi}\noindent
      \if\relax\detokenize{#1}\relax\else{\bfseries\paper@wpcjk\color{wpblue}#1}\enspace\fi}}
\else
  \newenvironment{keybox}[1][]{%
    \par\medskip\begin{list}{}{\leftmargin=1.5em\rightmargin=1.5em\listparindent=\parindent
      \parsep=0pt\topsep=0pt}\item\relax
    \if\relax\detokenize{#1}\relax\else\textbf{#1}\enspace\fi}%
    {\end{list}\medskip}
\fi

%% 理论模型推出命题后的解释正文：显式包裹，避免误把经验结果段落也改成楷体。
\newenvironment{theorytext}{%
  \begingroup
  \ifpaper@en\else\paperkaiti\fi
  \setlength{\parindent}{2em}%
  \setlength{\hangindent}{0pt}%
}{\par\endgroup}

%% ============================================================
%% 11. 底纹：复旦校徽 background.pdf，封面以外每页居中铺底，默认透明度 0.04
%% ============================================================
%% 文件查找：版本目录 → TEXINPUTS（.latexmkrc 已把 ../assets 与 ../_latex 加入搜索路径）。
%% 找不到就静默跳过，不报错。换图：换 paper/assets/background.pdf。
%%
%% 实现说明（改之前先读）：旧版用 TikZ 的 opacity 画水印。pgf 在 xdvipdfmx 下把透明度
%% 资源 /pgf@CA0.04 登记在"下一页"，于是每个画了水印的页面都引用一个不存在的
%% ExtGState——poppler 报 "ExtGState 'pgf@CA0.04' is unknown"，部分阅读器把校徽
%% 渲成不透明的黑块。现在改为每页自己声明透明度资源（pdf:put @resources）再引用，
%% 资源与引用在同一页，任何阅读器都一致。
\providecommand{\paperwatermarkopacity}{0.04}
\providecommand{\paperwatermarkfile}{background.pdf}
\newif\ifpaper@nowatermark \paper@nowatermarkfalse
\newcommand{\disablewatermark}{\global\paper@nowatermarktrue}
\newcommand{\enablewatermark}{\global\paper@nowatermarkfalse}
\ifpaper@wm
\AddToShipoutPictureBG{%
  \ifpaper@nowatermark\else
    \IfFileExists{\paperwatermarkfile}{%
      \special{pdf:put @resources <</ExtGState <</PaperWM
        <</ca \paperwatermarkopacity\space /CA \paperwatermarkopacity>> >> >>}%
      \AtPageCenter{\makebox[0pt]{\raisebox{-.5\height}{%
        \special{pdf:code q /PaperWM gs}%
        \includegraphics[width=\paperwidth]{\paperwatermarkfile}%
        \special{pdf:code Q}}}}%
    }{}%
  \fi}
\fi

%% ============================================================
%% 12. 封面信息（中英文同一张封面，只有行标签随语言切换）
%% ============================================================
\newcommand{\doctype}[1]{\def\@doctype{#1}}          %% 文稿类型：Proposal / 工作论文 / Working Paper
\newcommand{\assigntitle}[1]{\def\@assigntitle{#1}}  %% 主标题
\newcommand{\assignsubtitle}[1]{\def\@assignsubtitle{#1}}  %% 副标题（可空）
\newcommand{\authorname}[1]{\def\@authorname{#1}}
\newcommand{\affiliation}[1]{\def\@affiliation{#1}}  %% 单位
\newcommand{\course}[1]{\def\@course{#1}}            %% 课程（可空；空则封面不出这一行）
\newcommand{\submitdate}[1]{\def\@submitdate{#1}}
\let\coursetitle\doctype      %% 兼容旧稿
\let\studentid\affiliation    %% 兼容旧稿
%% wp 标题页专用（其他版式忽略）
\newcommand{\wpnumber}[1]{\def\@wpnumber{#1}}        %% 工作论文编号行（可空），如 Working Paper No. 2026-01
\newcommand{\wpshorttitle}[1]{\def\@wpshorttitle{#1}} %% 页眉短题名（可空 = 主标题）
\newcommand{\titlethanks}[1]{\def\@titlethanks{#1}}  %% 题名星号脚注：致谢、资助、免责声明（可空）
\newcommand{\wpcitation}[1]{\def\@wpcitation{#1}}    %% 目录页底部的建议引用格式（可空）
\wpnumber{}\wpshorttitle{}\titlethanks{}\wpcitation{}
\newcommand{\paper@runninghead}{\ifx\@wpshorttitle\@empty\@assigntitle\else\@wpshorttitle\fi}

\ifpaper@en
  \newcommand{\papernamelabel}{Name}
  \newcommand{\paperidlabel}{Affiliation}
  \newcommand{\papercourselabel}{Course}
  \newcommand{\paperdatelabel}{Date}
  \doctype{Working Paper}
\else
  \newcommand{\papernamelabel}{姓\quad 名}
  \newcommand{\paperidlabel}{单\quad 位}
  \newcommand{\papercourselabel}{课\quad 程}
  \newcommand{\paperdatelabel}{日\quad 期}
  \doctype{工作论文}
\fi
\assigntitle{Untitled}
\assignsubtitle{}
\authorname{}
\affiliation{}
\course{}
\submitdate{\today}

%% 封面徽标：logo.png（查找路径同底纹）；找不到则不出图
\providecommand{\paperlogofile}{logo.png}
\newcommand{\paper@logo}{%
  \IfFileExists{\paperlogofile}{\includegraphics[width=0.55\textwidth]{\paperlogofile}}{}}

%% ============================================================
%% 13. \makecover —— 封面（不铺底纹、不编页码）
%% ============================================================
\ifpaper@en
  \newcommand{\paper@covertitlefont}{\bfseries}
  \newcommand{\paper@coverinfofont}{\zihao{3}}
  \newcommand{\paper@coversep}{:\enspace}
\else
  \newcommand{\paper@covertitlefont}{\paperkaiti\bfseries}
  \newcommand{\paper@coverinfofont}{\zihao{2}\paperkaiti}
  \newcommand{\paper@coversep}{：}
\fi
\newcommand{\paper@coverrow}[2]{#1\paper@coversep & \underline{\makebox[180pt]{#2}}}
\ifpaper@wp
%% wp 封面（与标题页同一语汇）：上部徽标 → 锈红小型大写文稿类型 → 蓝色常规体大题名
%% （中文用楷体、不加粗）与斜体副标题 → 陶土色短线 → 下部"锈红标签 │ 内容"两列信息栏，
%% 中间一条陶土色细竖线，不用下划线填空格。三块之间按 1 : 1.4 : 0.8 的弹性留白。
\newcommand{\paper@wpcoverrow}[2]{%
  {\color{wprust}\paper@wpcjk #1} & #2 \\}
\newcommand{\makecover}{%
  \disablewatermark
  \begin{titlepage}
    \begin{center}
      \vspace*{0.6cm}%
      \IfFileExists{\paperlogofile}{\includegraphics[width=0.42\textwidth]{\paperlogofile}}{}\par
      \vspace{\stretch{1}}%
      {\large\paper@wpcjk\color{wprust}\scshape\MakeLowercase{\@doctype}\par}%
      \vspace{1.1cm}%
      {\setstretch{1.15}\huge\mdseries\paper@wpcjk\color{wpblue}\@assigntitle\par}%
      \ifx\@assignsubtitle\@empty\else
        \vspace{0.55cm}%
        {\setstretch{1.15}\Large\paper@wpcjk\color{wpblue}\ifpaper@en\itshape\fi\@assignsubtitle\par}%
      \fi
      \vspace{0.6cm}%
      {\color{wpclay}\rule{0.4\textwidth}{1.1pt}}\par
      \vspace{\stretch{1.4}}%
      {\large\renewcommand{\arraystretch}{1.75}%
       \begin{tabular}{@{}r@{\hspace{1.1em}}!{\color{wpclay}\vrule width 0.8pt}@{\hspace{1.1em}}l@{}}
         \paper@wpcoverrow{\papernamelabel}{\@authorname}
         \paper@wpcoverrow{\paperidlabel}{\@affiliation}
         \ifx\@course\@empty\else\paper@wpcoverrow{\papercourselabel}{\@course}\fi
         \paper@wpcoverrow{\paperdatelabel}{\@submitdate}
       \end{tabular}\par}%
      \vspace{\stretch{0.8}}%
    \end{center}
  \end{titlepage}%
  \enablewatermark
}
\else
\newcommand{\makecover}{%
  \disablewatermark
  \begin{titlepage}
    \begin{center}
      \vspace*{0pt}\vspace{\stretch{1}}
      \paper@logo\par
      \vspace{1.6cm}
      {\zihao{1}\bfseries\rmfamily \@doctype\par}
      \vspace{1.0cm}
      {\zihao{2}\paper@covertitlefont \@assigntitle\par}
      \ifx\@assignsubtitle\@empty\else
        \vspace{0.4cm}
        {\zihao{-2}\paper@covertitlefont \@assignsubtitle\par}
      \fi
      \vspace{2.4cm}
      {\paper@coverinfofont
       \renewcommand{\arraystretch}{1.8}%
       \begin{tabular}{r@{}l}
         \paper@coverrow{\papernamelabel}{\@authorname} \\[0.8cm]
         \paper@coverrow{\paperidlabel}{\@affiliation} \\[0.8cm]
         \ifx\@course\@empty\else
         \paper@coverrow{\papercourselabel}{\@course} \\[0.8cm]
         \fi
         \paper@coverrow{\paperdatelabel}{\@submitdate} \\
       \end{tabular}\par}
      \vspace{\stretch{2}}
    \end{center}
  \end{titlepage}%
  \enablewatermark
}
\fi

%% ============================================================
%% 13.5 \makewptitle —— wp 标题页（替代封面；页码从标题页的 1 起，连续编到底）
%% ============================================================
%% 题名蓝色（星号脚注来自 \titlethanks）→ 陶土色短线 → 作者 → 单位 / 编号 / 日期 → 摘要。
%% 摘要用标准 abstract 环境，写在 \makewptitle 之后、\clearpage 之前，与题名脚注同页。
\ifpaper@wp
  \newcommand{\makewptitle}{%
    \thispagestyle{plain}%
    \begin{center}
      \vspace*{2.2em}%
      {\LARGE\color{wpblue}\@assigntitle
        \ifx\@titlethanks\@empty\else\textsuperscript{\normalcolor\normalsize *}\fi\par}%
      \ifx\@assignsubtitle\@empty\else
        \vspace{0.5em}{\large\color{wpblue}\@assignsubtitle\par}%
      \fi
      \vspace{1.7em}%
      {\color{wpclay}\rule{1.2in}{1.1pt}}\par
      \vspace{1.6em}%
      {\large\@authorname\par}%
      \vspace{1.2em}%
      {\setstretch{1.25}\small
        \ifx\@affiliation\@empty\else\@affiliation\par\fi
        \ifx\@wpnumber\@empty\else\@wpnumber\par\fi
        \@submitdate\par}%
    \end{center}
    \ifx\@titlethanks\@empty\else
      %% 直接调底层脚注（hyperref 保存为 \H@@footnotetext）：星号没有对应的正文锚点，
      %% 走 \footnotetext 会触发 hyperref 的 empty anchor 警告，也不占用脚注计数
      \begingroup
        \def\@thefnmark{*}%
        \@ifundefined{H@@footnotetext}{\@footnotetext}{\H@@footnotetext}{\@titlethanks}%
      \endgroup
    \fi
  }
  \renewenvironment{abstract}{%
    \par\vspace{0.2em}%
    \begin{center}{\small\bfseries\paper@wpcjk\color{wpblue}\abstractname}\end{center}%
    \vspace{-0.4em}%
    \begin{list}{}{\leftmargin=27pt\rightmargin=27pt\listparindent=\ifpaper@en 1.3em\else 2em\fi
      \parsep=0pt\topsep=0pt\itemindent=0pt}%
    \item\relax\setstretch{1.12}\small}%
    {\end{list}}
\else
  \newcommand{\makewptitle}{\ClassError{paper}{\string\makewptitle\space 需要 wp 选项}
    {用 \string\documentclass[en,wp]{paper}；其他版式用 \string\makecover。}}
\fi

%% ============================================================
%% 14. \maketoc —— 目录（不编页码、条目黑色；正文页码从 1 起）
%%     wp：目录页照常带页眉页码、不重置页码；\wpcitation 非空时排在页底
%% ============================================================
\ifpaper@wp
\newcommand{\maketoc}{%
  \tableofcontents\thispagestyle{fancy}%
  \ifx\@wpcitation\@empty\else
    \vfill\noindent{\small\setstretch{1.15}\@wpcitation\par}%
  \fi
  \clearpage
}
\else
\newcommand{\maketoc}{%
  \pagestyle{empty}%
  {\hypersetup{linkcolor=black}\tableofcontents}%
  \clearpage
  \setcounter{page}{1}%
  \pagestyle{fancy}%
}
\fi

%% ============================================================
%% 15. 参考文献：类代为加载 biblatex（nobib 选项可关）
%%     中文稿 → paper-sjjj（《世界经济》）；英文稿 → paper-aer（AER）
%%     main.tex 只需 \addbibresource{../references.bib} 与 \printbibliography
%% ============================================================
\ifpaper@bib
  \ifpaper@en
    \def\paper@bibstyle{paper-aer}
  \else
    \def\paper@bibstyle{paper-sjjj}
  \fi
  %% autopunct=false：否则 biblatex 会把紧跟引用的 . , ; 吞进引用里，一起染成蓝色
  \RequirePackage[backend=biber, style=\paper@bibstyle, natbib=true,
                  sortlocale=zh__pinyin, autolang=none, autopunct=false]{biblatex}
  \ifpaper@en
    \def\paper@bibtitle{References}
  \else
    \def\paper@bibtitle{参考文献}
  \fi
  \defbibheading{bibliography}[\paper@bibtitle]{%
    \section*{#1}\addcontentsline{toc}{section}{#1}\markboth{#1}{#1}}
  \ifpaper@wp
    %% 正文字号、条目间留白（参照 PDF）；先 \setstretch 后改字号，见 §5
    \ifpaper@en
      \AtBeginBibliography{\setstretch{1.12}\normalsize}
      \setlength{\bibhang}{1.5em}
    \else
      \AtBeginBibliography{\setstretch{1.3}\normalsize}
      \setlength{\bibhang}{2em}
    \fi
    \setlength{\bibitemsep}{0.55em}
  \else\ifpaper@en
    \AtBeginBibliography{\zihao{5}\setstretch{1.2}}
    \setlength{\bibhang}{1.5em}
    \setlength{\bibitemsep}{0.15em}
  \else
    \AtBeginBibliography{\zihao{5}\setstretch{1.3}}
    \setlength{\bibhang}{2em}
    \setlength{\bibitemsep}{0.15em}
  \fi\fi
\fi

\endinput
