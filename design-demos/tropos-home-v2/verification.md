# 方向稿检查（第二轮 · tropos-home-v2）

- 三版完整稿 + 六格缩略图均经本地 HTTP 服务在真实浏览器打开检查。
- 桌面 1440px：A/B/C 首屏与整页长截图正常（shots/ 目录）。
- 移动 375px：三版 `scrollWidth === innerWidth`，无横向溢出。
- A：四个引擎章节、希腊词源、三拍叙事、引言区均渲染；滚动 reveal 正常。
- B：四色气流 canvas 动画、金点脉冲、闭环管线、代码卡、事件 chips 均渲染。
- C：「工资延迟 15 天」按钮可点，甲乙两栏第 3 项按计划变更并高亮，KAIROS 提示条出现。
- 字体：中文宋体走系统 Songti SC/Noto Serif 栈，UI 字体引用项目本地 Noto Sans SC + Manrope（离线可用）。
- 设计 lint 因本机缺 Python Playwright 跳过，以上检查以 playwright-cli 实测替代。
- 正式 `index.html` 与后台未修改，等待方向选定。
