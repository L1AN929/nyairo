"""Embedded, bounded static pictures. SVG uses a deliberately small allowlist."""
import base64
import re
import struct
import uuid
import zlib
import xml.etree.ElementTree as ET
from legacy import fail, fields, text, integer, ident

MAX_IMAGE=524288
KINDS={'chair','bed','book','cup','cabinet','table','plant','dispenser','lamp'}

def picture(mime, encoded):
    if mime not in ('image/png','image/webp','image/svg+xml'): fail('图片仅支持静态 PNG、WebP 和 SVG。')
    if not isinstance(encoded,str) or len(encoded)>MAX_IMAGE*4//3+8: fail('单张图片最多 512 KiB。')
    try: data=base64.b64decode(encoded,validate=True)
    except ValueError: fail('图片编码损坏。')
    if not 0<len(data)<=MAX_IMAGE: fail('图片为空或超过 512 KiB。')
    if mime=='image/svg+xml':
        if re.search(br'<!DOCTYPE|<!ENTITY',data,re.I): fail('SVG 不允许文档实体。')
        try: root=ET.fromstring(data)
        except ET.ParseError: fail('SVG XML 无效。')
        tags={'svg','g','path','rect','circle','ellipse','line','polyline','polygon','defs','linearGradient','radialGradient','stop','text','tspan','title','desc'}
        attrs={'id','viewBox','width','height','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','d','points','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-dasharray','opacity','fill-opacity','stroke-opacity','transform','offset','stop-color','stop-opacity','gradientUnits','gradientTransform','font-size','font-family','text-anchor','fill-rule','preserveAspectRatio'}
        nodes=list(root.iter())
        if len(nodes)>2000: fail('SVG 节点过多。')
        for node in nodes:
            tag=node.tag.removeprefix('{http://www.w3.org/2000/svg}')
            if tag not in tags: fail('SVG 含不支持的主动或复杂元素：'+tag)
            for key,value in node.attrib.items():
                if key not in attrs or re.search(r'javascript:|https?:|file:|data:|@import',value,re.I): fail('SVG 不允许脚本、事件、样式表或外部引用。')
                if 'url(' in value.lower() and not re.fullmatch(r'url\(#[a-zA-Z0-9_-]+\)',value): fail('SVG 仅允许内部渐变引用。')
        if root.tag.removeprefix('{http://www.w3.org/2000/svg}')!='svg': fail('SVG 根元素无效。')
        try:
            if 'viewBox' in root.attrib:
                dims=[float(v) for v in re.split(r'[ ,]+',root.attrib['viewBox'].strip())]
                if len(dims)!=4: raise ValueError()
                w,h=dims[2:]
            else: w,h=float(root.get('width','100')),float(root.get('height','100'))
        except ValueError: fail('SVG 需要数值尺寸或 viewBox。')
    elif mime=='image/png':
        if data[:8]!=b'\x89PNG\r\n\x1a\n': fail('PNG 文件头无效。')
        offset=8;w=h=0;image=False;ended=False
        while offset+12<=len(data):
            n=int.from_bytes(data[offset:offset+4],'big');tag=data[offset+4:offset+8];chunk=data[offset+8:offset+8+n]
            if offset+12+n>len(data) or zlib.crc32(tag+chunk)!=int.from_bytes(data[offset+8+n:offset+12+n],'big'): fail('PNG 数据块损坏。')
            if offset==8:
                if tag!=b'IHDR' or n!=13: fail('PNG 缺少尺寸。')
                w,h=struct.unpack('>II',chunk[:8])
            if tag==b'acTL': fail('不支持动画 PNG。')
            if tag==b'IDAT': image=True
            offset+=12+n
            if tag==b'IEND': ended=True;break
        if not image or not ended or offset!=len(data): fail('PNG 数据不完整。')
    else:
        if data[:4]!=b'RIFF' or data[8:12]!=b'WEBP' or int.from_bytes(data[4:8],'little')+8!=len(data): fail('WebP 容器无效。')
        offset=12;w=h=0;image=False
        while offset+8<=len(data):
            tag=data[offset:offset+4];n=int.from_bytes(data[offset+4:offset+8],'little');chunk=data[offset+8:offset+8+n]
            if len(chunk)!=n: fail('WebP 数据不完整。')
            if tag in (b'ANIM',b'ANMF'): fail('不支持动画 WebP。')
            if tag==b'VP8X' and n==10:
                if chunk[0]&2: fail('不支持动画 WebP。')
                w,h=1+int.from_bytes(chunk[4:7],'little'),1+int.from_bytes(chunk[7:10],'little')
            if tag==b'VP8L' and n>=5 and chunk[0]==0x2f:
                bits=int.from_bytes(chunk[1:5],'little');iw,ih=(bits&0x3fff)+1,((bits>>14)&0x3fff)+1;image=True
                if not w: w,h=iw,ih
            if tag==b'VP8 ' and n>=10 and chunk[3:6]==b'\x9d\x01\x2a':
                iw,ih=int.from_bytes(chunk[6:8],'little')&0x3fff,int.from_bytes(chunk[8:10],'little')&0x3fff;image=True
                if not w: w,h=iw,ih
            offset+=8+n+(n%2)
        if not image or offset!=len(data): fail('WebP 缺少静态图像。')
    if not 1<=w<=2048 or not 1<=h<=2048: fail('图片尺寸需为 1–2048 像素。')
    return int(w),int(h),len(data)

def validate_asset(a):
    fields(a,['id','name','category','collection','mime','data','width','height','kind','visual','interactive'],'图片素材')
    ident(a['id'],'素材 ID')
    for k in ('name','category'): text(a[k],'素材'+k,80)
    text(a['collection'],'素材集合',80,True)
    if a['kind'] not in KINDS or type(a['interactive']) is not bool: fail('素材物件类型或交互开关无效。')
    fields(a['visual'],['scale','rotation','layer','show_name'],'素材视觉')
    for k,lo,hi in [('scale',40,200),('rotation',0,359),('layer',-100,100)]: integer(a['visual'][k],k,lo,hi)
    if type(a['visual']['show_name']) is not bool: fail('素材名称开关无效。')
    w,h,size=picture(a['mime'],a['data'])
    if (a['width'],a['height'])!=(w,h): fail('素材尺寸与图片不一致。')
    return size

def import_asset(data):
    w,h,_=picture(data.get('mime'),data.get('data'))
    a={'id':'asset-'+uuid.uuid4().hex[:12],'name':data.get('name','自定义素材'),'category':data.get('category','家具'),'collection':data.get('collection',''),'mime':data['mime'],'data':data['data'],'width':w,'height':h,'kind':data.get('kind','table'),'interactive':data.get('interactive',False),'visual':{'scale':100,'rotation':0,'layer':0,'show_name':True}}
    validate_asset(a);return a
