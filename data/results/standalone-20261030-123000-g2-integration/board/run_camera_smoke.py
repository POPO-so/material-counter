import sys
from pathlib import Path
root=Path('/home/xilinx/standalone-g25-20261030');sys.path.insert(0,str(root))
if __name__=='__main__':
    from scripts.standalone.g1_run import main
    main(['--execute','--manifest',str(root/'export/display-manifest.json'),
          '--manifest-sha256','2872e1598b3c3ab7ccb5d383f5cee6a8822dbd91eb0f198098eca067489a2966',
          '--boot-id','e48ff153-bd85-4dd6-98f6-438bd51da08d',
          '--output',str(root/'g2-camera-smoke.json'),'--source','camera','--duration','60','--fps','20'])
