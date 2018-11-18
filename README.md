## 为什么要做？

因为某天一个朋友被学校要求在动态里面刷某种言论的时候，打开某网站后被傻逼到了

**欢迎加入习语学习群**：[点击加入](https://t.me/joinchat/CMe2Rlc9vN3xQViQUVgXxg)

每日习语学习

好好学习天天向上

习得者得永生

## 啥？

![photo_2018-11-14_12-08-54.jpg](https://i.loli.net/2018/11/15/5bec56b01d466.jpg)

http://jhsjk.people.cn

## 好的？

嗯。

API上线了（使用方法可参考[这里](https://blog.lwl12.com/read/hitokoto-api.html)）：https://xixi-haha.never.eu.org

Telegram Bot: [@xixi_haha_bot](https://t.me/xixi_haha_bot)

托管在AWS Lambda

## 还有什么说的吗

这个版本没有太注重分句，主要是清理了一下内容。抓取脚本（XJB）参考在这里：

```python3
import json
from bs4 import BeautifulSoup
import requests


li = []
sentences = []

# Get ALL Articles
for i in range(1, 16):
    print(i)
    result = requests.get(
        "http://jhsjk.people.cn/result/{}?form=706&else=501".format(i))
    soup = BeautifulSoup(result.content)
    arr = soup.find_all('ul', class_="p1_2")[0].find_all('a')
    for a in arr:
        li.append('http://jhsjk.people.cn/' + a['href'])

print('start fetching pages')
for addr in li:
    print(addr)
    result = requests.get(addr)
    soup = BeautifulSoup(result.content)
    arr = soup.find_all(class_="d2txt_con")[0].find_all('p')

    for i in arr:
        sentences.append(i.text.strip())


with open('xi.json', 'w') as outfile:
    json.dump(sentences, outfile, indent=2, ensure_ascii=False)

```

清理：
```python3
import json

with open('xi-v1.json') as f:
    data = json.load(f)

sentences = []

# extend data
# \r\r\n is replaced by hand
# (（新)(.*）) for attr
# (新华社记者)(.*摄)
# for i in data:
#     li = i.split('\n')
#     for s in li:
#         s = s.strip()
#         if s is not "":
#             if s.startswith('（新') is False and s.startswith('(') is False and s.startswith('（2') is False and s.startswith('《 ') is False and s.startswith('>') is False:
#                 sentences.append(s)

for s in data:
    if s.startswith('（人') is False and s.startswith('(') is False and s.startswith('（2') is False and s.startswith('《 ') is False and s.startswith('(2') is False:
        sentences.append(s)
    else:
        print(s)

# a = list(set(sentences)) 

with open('xi-v2.json', 'w') as outfile:
    json.dump(sentences, outfile, indent=2, ensure_ascii=False)
```

服务端
```node
// content of index.js
const http = require('http')
const port = 3000

const json = require('./xi.json')

const requestHandler = (request, response) => {
  response.writeHead(200,{
	  'access-control-allow-headers': 'Content-Type, Content-Length, Authorization, Accept, X-Requested-With',
	  'access-control-allow-methods': 'POST, GET',
	  'access-control-allow-origin': '*',
	  'content-type': 'text/html; charset=utf-8',
  })
  response.end(json[Math.floor(Math.random() * json.length)])
}

const server = http.createServer(requestHandler)

server.listen(port, (err) => {
  if (err) {
    return console.log('something bad happened', err)
  }

  console.log(`server is listening on ${port}`)
})

```

## 没了？

本来是想保留那些重复来重复去的玩意，但是抓的时候有些地方漏了，就直接全部去重干掉了。有兴趣的自己玩玩吧。
