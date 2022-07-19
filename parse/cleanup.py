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