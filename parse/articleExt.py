from bs4 import BeautifulSoup
import json

with open("entries.json") as json_file:
    articles = json.load(json_file)
    
folder = "articles/"
res = []

for i in articles:
    aid = i['article_id']
    print(aid)
    with open(folder + aid) as html:
        soup = BeautifulSoup(html.read(), features="lxml")
        art = soup.find_all(class_="d2txt_con")[0]
        txt = [x.strip() for x in art.get_text().strip().split('\n')]
        txt = list(filter(None, txt))
        editor = soup.find_all(class_="editor")
        if len(editor) > 0:
            editor = editor[0].get_text().strip()
        else:
            editor = "不明"

        res.append({
            'title': i['title'],
            'date': i['input_date'],
            'id': aid,
            'author': i['origin_name'],
            'article': str(art),
            "editor": editor,
            'text': txt
        })

with open("result.json", 'w', encoding='utf-8') as f:
    json.dump(res, f, ensure_ascii=False)

with open("result-min.json", 'w', encoding='utf-8') as f:
    dic = dict()
    for i in res:
        dic[i['id']] = {
            'title': i['title'],
            'date': i['date'],
            'author': i['author'],
            "editor": i['editor'],
            'text': i['text']
        }
    json.dump(dic, f, ensure_ascii=False)