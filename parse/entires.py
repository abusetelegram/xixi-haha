import json
import os
import asyncio
import aiohttp
import requests

def getUrl(page=1):
    return "http://jhsjk.people.cn/testnew/result?page={}&source=2".format(page)

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

response = requests.get(getUrl(), headers=headers)
data_json = json.loads(response.text)
folder = "api/"
total_page = int(data_json['total'])
print("total article: ", total_page, ", assuming 10 per page")
downloaded = set(os.listdir(folder))
concur_num = 5

def getAllNeedToDownload(downloaded, total_page):
    total = set([str(i) for i in range(1, (total_page // 10) + 2)])
    return list(total - downloaded)
        

async def get(page, session):
    try:
        async with session.get(url=getUrl(page)) as response:
            resp = await response.read()
            d = json.loads(resp)
            if len(d['list']) == 0:
                print(page, " unable to download, len 0")
            else:
                with open(folder + page, 'w', encoding='utf-8') as f:
                    json.dump(d['list'], f, ensure_ascii=False)
    except Exception as e:
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

pages = getAllNeedToDownload(downloaded, total_page)
print("need to download ", len(pages), " pages")
asyncio.run(main(pages))

## Combining
downloaded = set(os.listdir(folder))

res = []

for i in downloaded:
    with open(folder + i) as json_file:
        res += json.load(json_file)

print("Result has ", len(res), " entries, total page num is ", total_page)

with open("entries.json", 'w', encoding='utf-8') as f:
    json.dump(res, f, ensure_ascii=False)