"""Exact point-depth indices shared by Prescribed and Manual CPU analysis."""
import operator
import numpy as np

# Conservative bound: UE's recorded desktop RHI has MAX_TEXTURE_MIP_COUNT=15
# (16384); even a 32768 axis keeps the shader's signed numerator below INT_MAX.
MAX_VIEW_DIMENSION = 32768


def axis_indices(view_size, fog_size):
    """Local floor((pixel+1/2)*view/fog), without a floating intermediate."""
    view_size, fog_size = operator.index(view_size), operator.index(fog_size)
    assert 1 <= fog_size <= view_size <= MAX_VIEW_DIMENSION, 'unsupported depth sampling dimensions'
    pixels = np.arange(fog_size, dtype=np.int64)
    return ((2*pixels+1)*view_size)//(2*fog_size)


def scene_indices(meta):
    """scene-data.bin already contains only the active ViewRect; do not add ViewMin."""
    vw,vh = meta['viewSize']; fw,fh = meta['fogSize']
    return np.meshgrid(axis_indices(vw,fw), axis_indices(vh,fh))
