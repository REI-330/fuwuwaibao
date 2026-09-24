import argparse,urllib.request,json
from pathlib import Path
URLS={'onet':'https://www.onetcenter.org/dl_files/database/db_31_0_text.zip','esco':'https://esco.ec.europa.eu/system/files/2024-05/ESCO%20dataset%20-%20v1.2.0%20-%20CSV.zip'}
p=argparse.ArgumentParser(); p.add_argument('--source',choices=URLS,required=True); p.add_argument('--output',required=True); a=p.parse_args(); Path(a.output).parent.mkdir(parents=True,exist_ok=True); urllib.request.urlretrieve(URLS[a.source],a.output); print(json.dumps({'source':a.source,'output':a.output}))
