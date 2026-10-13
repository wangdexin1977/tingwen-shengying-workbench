# 听文声影工作台 · 产品官网

公开地址：https://tingwen-creator-studio-102.pages.dev/

产品基线1.2.2，内容更新2026-10-13。网站面向自媒体人介绍文案到MP3／MP4、录音克隆、配图字幕音乐、长篇和夜间队列及头条交接。它是产品介绍与文档站，不在云端替用户运行本机大模型。

## 内容来源

- `../docs/产品规格说明书.md`：当前规格的唯一正文来源，26个主体章节与参数、接口附录。
- `../docs/接口与参数清单.json`：从应用源码提取的46条接口声明和默认参数，不包含用户配置或密钥。
- `src/home.html`：当前首页及自媒体使用价值。
- `src/faq.json`、`src/shengying-facts.json`：当前FAQ与可引用事实。
- `src/site.css`、`src/site.js`：响应式布局、联系弹窗、复制、文档目录等交互。
- `src/cases.json`和案例图片：保留原有8个公开头条题材参考，不据此承诺制作来源或商业效果。
- `content-source/`：原PC／Android的45章正文归档，原正文保留，发布页增加历史范围标记。

`tools/build-site.mjs`生成静态HTML、规范URL、JSON-LD、sitemap、事实JSON、AI阅读资料和历史章节。`tools/build-specification.mjs`使用Marked将规格正文编译为在线／离线HTML，复制原始Markdown和接口JSON，并记录正文SHA-256。`tools/prepare-counter-content.mjs`准备内容路由；`tools/build-cloudflare.mjs`将内容和访问服务打包为Pages Advanced Mode Worker。

## 本机开发与检查

Node.js >=22.13，Google Chrome用于浏览器测试。npm依赖版本记录在package-lock.json。安装依赖、构建和内容检查：

```sh
npm ci
npm run build
npm test
```

首次本地预览需要创建本地D1表，只作用于本地 `.wrangler`：

```sh
npx wrangler d1 migrations apply tingwen-creator-studio-102-visits --local
npm run dev
```

预览地址http://127.0.0.1:18082/。预览运行时执行：

```sh
TINGWEN_QA_ORIGIN=http://127.0.0.1:18082 TINGWEN_COUNTER_QA=1 node --test tests/browser.test.mjs
```

检查360／390／1440三个宽度的9种页面，共27组；包括移动布局、完整正文、目录跳转、打印、无脚本阅读、联系复制、样音、案例图片及结构化数据。`npm test`还核对正文SHA-256、全部内链、原45章逐章文本一致性、首页和模型事实、访问统计并发与故障处理。

## 重新发布到原站点

```sh
npm run build
npm test
npm run deploy
```

Wrangler使用用户已有本机授权，目标始终为 `tingwen-creator-studio-102`、生产分支main。GitHub提交不会自动替代这一步发布。`wrangler.toml`保持原D1绑定，正常再次部署无需重新初始化生产数据库，访问总数与started_at应继续保留。

累计统计采用成功内容GET请求，包含真人、AI及搜索访问，重复打开计入，不是去重人数。HEAD、静态图片／CSS／音频、重定向、404、只读统计API不增加。数据库故障显示“暂不可用”；不存储IP或身份，不提供公开重置接口。

## 生成内容与版本边界

当前规格在 `/specification/`，全文下载位于 `/downloads/shengying-specification.md` 与 `.html`；机器清单为 `/downloads/shengying-api.json`。规范页面52个，HTML文件56个，内容路由111条。旧45章与旧离线文档明确标注历史，不能把其试用规则或“不支持字幕／音乐”等结论套用于本机整合版。

网页只引用公开产品信息。音色、录音、稿件、成品、API Key、浏览器登录资料、模型、`.wrangler`本地数据库、node_modules和QA截图不上传GitHub。
