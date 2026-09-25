## 为什么要做？

因为某天一个朋友被学校要求在动态里面刷某种言论的时候，打开某网站后被傻逼到了

**欢迎加入习语学习群**：[点击加入](https://t.me/xiyuxuexi)

每日习语学习

好好学习天天向上

习得者得永生

## 啥？

![photo_2018-11-14_12-08-54.jpg](https://i.loli.net/2018/11/15/5bec56b01d466.jpg)

http://jhsjk.people.cn

## 好的？

嗯。

Telegram Bot: [@xixi_haha_bot](https://t.me/xixi_haha_bot)

支持`inline mode`

## 数据与下载

机器可读的权威语料位于独立的 [`data` 分支](https://github.com/abusetelegram/xixi-haha/tree/data/articles)，每篇文章一个 JSON 文件，并遵守 add-only 规则：

- [浏览 `articles/`](https://github.com/abusetelegram/xixi-haha/tree/data/articles)
- [下载整个 data 分支](https://github.com/abusetelegram/xixi-haha/archive/refs/heads/data.zip)
- [查看数据更新和可下载聚合文件](https://github.com/abusetelegram/xixi-haha/actions/workflows/update-data.yml)（成功运行的 Actions artifact 包含 minimal/full JSON、TGZ 和 provenance）

GitHub Pages 是可选展示入口，不是数据读取前提。仓库中的旧
`parse/result-min.json` 和 `parse/result-full.tgz` 暂时保留用于首次导入和兼容；验证新流程后再单独决定移除。`parse/v1/xi.json` 与 `web/` 的旧数组格式保持不变。

## 更新数据

抓取代码与数据 checkout 必须分开；所有数据命令都显式传入 data worktree。安装和导入、增量补齐、全量核对、确定性导出的完整用法见 [parse/README.md](./parse/README.md)。

```bash
uv sync --locked
uv run --locked python parse/update.py update \
  --data-dir /absolute/path/to/xixi-haha-data \
  --max-pages 50 --max-additions 200 \
  --delay 1 --timeout 30 --retries 3
uv run --locked python parse/corpus.py validate \
  --data-dir /absolute/path/to/xixi-haha-data
```

每周自动流程只新增文章，遇到不完整扫描、已有文章变化、删除、推送竞争或导出失败都会停止。聚合文件由精确的 data commit 生成并作为 GitHub Actions artifact 提供，不提交到 data 分支。

## Web服务

见`web/`

## 没了？

（其实我是有Future Plan的，但是我太懒了……估计会在某个闲得无聊的夜晚做了）

- 点赞，鼓掌👏
- 配合**原声**音频（对的，有很多**亲自朗读**的）

本来是想保留那些重复来重复去的（比如称呼，xxx好）玩意，可以显得很zz。但是抓的时候有些地方漏了，就直接全部去重干掉了。有兴趣的自己玩玩吧。
