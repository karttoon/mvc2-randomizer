import numpy as np
def _morton_tables(n):
    i=np.arange(n)
    x=i.astype(np.uint32)
    def spread(v):
        v=(v|(v<<8))&0x00FF00FF; v=(v|(v<<4))&0x0F0F0F0F
        v=(v|(v<<2))&0x33333333; v=(v|(v<<1))&0x55555555
        return v
    return spread(x)
def twiddle_index(w,h):
    """return array idx[y,x] -> linear texel index in twiddled data"""
    n=min(w,h)
    sx=_morton_tables(n); sy=_morton_tables(n)
    yy,xx=np.meshgrid(np.arange(n),np.arange(n),indexing='ij')
    blk=(sy[yy]|(sx[xx]<<1)).astype(np.uint32)     # y even bits, x odd bits
    idx=np.zeros((h,w),np.uint32)
    if w>=h:
        for bx in range(w//n):
            idx[:, bx*n:(bx+1)*n]=blk+bx*n*n
    else:
        for by in range(h//n):
            idx[by*n:(by+1)*n, :]=blk+by*n*n
    return idx
def decode(data,w,h,fmt):
    px=np.frombuffer(data[:w*h*2],'<u2')
    idx=twiddle_index(w,h)
    t=px[idx]
    out=np.zeros((h,w,4),np.uint8)
    if fmt==1:   # RGB565
        out[...,0]=((t>>11)&0x1F)*255//31; out[...,1]=((t>>5)&0x3F)*255//63
        out[...,2]=(t&0x1F)*255//31; out[...,3]=255
    elif fmt==0: # ARGB1555
        out[...,0]=((t>>10)&0x1F)*255//31; out[...,1]=((t>>5)&0x1F)*255//31
        out[...,2]=(t&0x1F)*255//31; out[...,3]=np.where((t>>15)&1,255,0)
    else:        # ARGB4444
        out[...,0]=((t>>8)&0xF)*17; out[...,1]=((t>>4)&0xF)*17
        out[...,2]=(t&0xF)*17; out[...,3]=((t>>12)&0xF)*17
    return out[::-1]        # vertical flip (verified against ModNao)
def encode(img,fmt):
    a=img[::-1]
    h,w=a.shape[:2]
    r=a[...,0].astype(np.uint32); g=a[...,1].astype(np.uint32); b=a[...,2].astype(np.uint32); al=a[...,3].astype(np.uint32)
    if fmt==1:   t=((r>>3)<<11)|((g>>2)<<5)|(b>>3)
    elif fmt==0: t=((al>>7)<<15)|((r>>3)<<10)|((g>>3)<<5)|(b>>3)
    else:        t=((al>>4)<<12)|((r>>4)<<8)|((g>>4)<<4)|(b>>4)
    idx=twiddle_index(w,h)
    flat=np.zeros(w*h,'<u2')
    flat[idx.ravel()]=t.ravel().astype('<u2')
    return flat.tobytes()
