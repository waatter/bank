# 本次技术文档核验

日期：2026-11-02。当前后端基线2a33063；包含Kimi制作的本地界面更改，非全干净发布提交。

## 运行结果
- Python unittest发现470项，成功469，跳过1，失败/错误0。跳过为Windows专属目录权限测试。
- Node --test tests/*.cjs：运行器101项，101通过，无失败/跳过。
- 默认规则模式红队：62攻击/14对照全部达到预期。未使用真实模型、未创建真实美团订单。
- 这些层有重叠，不相加成独立测试总数。

## AI Squad
通过ai-squad实际调用Codex CLI（返回gpt-6-astra）、Gemini（gemini-4-argon-medium）、DeepSeek Flash。三路ok=true仅表示通道完成，内容由协调者审核。两条原生Codex子任务仅给源码和核查规格，独立核对核心算法与外部集成。

采纳：补立意/职责映射；区分合成银行与真实美团；测试总数口径和版本归属；准确描述任务executed与部分失败；金额及预算规格；代理与运行时区别；对用户输入中的敏感信息保留说明。

未采纳：Gemini建议删除未知攻击边界和四角色非独立模型说明；删除会误导，因此保留并具体化。DeepSeek草案中自动提高现金底线权重、恢复与保护的时间顺序未经代码支持，未直接采用。

## 文件
- source-manifest.json：文档核验时相关源码指纹（不包含密钥或数据库）。
- python-tests.log、frontend-tests.log：本次真实运行日志。
- redteam-current.json/md：独立保存的当前规则模式红队报告，未覆盖仓库历史报告。

测试工具链使用本机Codex捆绑运行时；构建镜像声明Python3.13与Node22，本次实跑Python3.12.14与Node24.22.1，不能混淆。

## 并行界面迭代
核验期间另一设计会话继续修改 assets/typography.css 与 assets/demo.css。post-test-observation.json 记录该观察；后端与JavaScript指纹在该时点未变。本次日志不代表随后CSS更改的复验。
