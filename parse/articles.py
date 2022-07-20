import json
import os
import asyncio
import aiohttp
import requests

def getArtUrl(id):
    return "http://jhsjk.people.cn/article/{}".format(id)

headers = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.7,zh-CN;q=0.3',
    'Accept-Encoding': 'gzip, deflate',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cookie': 'ci_session=afurp2kbinrnsmsq56abkbhf2ogvvs3r; __jsluid_h=8411536817b1b92b277fb3327be8cdf1',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0'
}

folder = "articles/"
articles = []
with open("entries.json") as json_file:
    articles = json.load(json_file)
print("total article: ", len(articles))
downloaded = set(os.listdir(folder))
concur_num = 10

def getAllNeedToDownload(downloaded, articles):
    ids = set([i['article_id'] for i in articles])
    return list(ids - downloaded)
        

async def get(page, session):
    try:
        async with session.get(url=getArtUrl(page)) as response:
            resp = await response.text()
            with open(folder + page, 'w', encoding='utf-8') as f:
                f.write(resp)
    except Exception as e:
        print(e)
        print("Unable to get url {} due to {}.".format(page, e.__class__))

async def gather_with_concurrency(n, *tasks):
    semaphore = asyncio.Semaphore(n)

    async def sem_task(task):
        async with semaphore:
            return await task
    return await asyncio.gather(*(sem_task(task) for task in tasks))

async def main(pages):
    async with aiohttp.ClientSession(headers=headers) as session:
        await gather_with_concurrency(concur_num, *[get(page, session) for page in pages])

pages = getAllNeedToDownload(downloaded, articles)
print("need to download ", len(pages), " pages")
asyncio.run(main(pages))