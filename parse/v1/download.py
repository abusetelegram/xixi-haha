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
