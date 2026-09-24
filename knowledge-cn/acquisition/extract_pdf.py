import argparse, json
from pathlib import Path
from pypdf import PdfReader

parser=argparse.ArgumentParser(); parser.add_argument('--pdf',required=True); parser.add_argument('--source-id',required=True); parser.add_argument('--output',required=True); args=parser.parse_args()
reader=PdfReader(args.pdf); out=[]
for i,page in enumerate(reader.pages):
 text=(page.extract_text() or '').strip()
 if text: out.append({'id':f'{args.source_id}-page-{i+1}','type':'source_page','title':f'{args.source_id} 第{i+1}页','content':text,'source':{'id':args.source_id,'page':i+1},'record_kind':'FACT_CANDIDATE','review_status':'PENDING'})
Path(args.output).parent.mkdir(parents=True,exist_ok=True); Path(args.output).write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in out)+'\n',encoding='utf8'); print(json.dumps({'pages':len(reader.pages),'nonempty':len(out)},ensure_ascii=False))
