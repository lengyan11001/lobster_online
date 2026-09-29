from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    Gray, _lcid, FONTUNDERSCORE, OLE_HANDLE, IFontEventsDisp,
    DISPMETHOD, IFont, IUnknown, OLE_XPOS_CONTAINER, FontEvents,
    Default, FONTSIZE, OLE_XSIZE_CONTAINER, IPicture,
    OLE_YSIZE_CONTAINER, StdFont, Unchecked, Color, HRESULT,
    OLE_XPOS_HIMETRIC, OLE_YSIZE_HIMETRIC, OLE_XSIZE_HIMETRIC, GUID,
    typelib_path, OLE_ENABLEDEFAULTBOOL, CoClass, StdPicture,
    OLE_YPOS_HIMETRIC, EXCEPINFO, IEnumVARIANT, COMMETHOD, IFontDisp,
    FONTITALIC, IDispatch, OLE_CANCELBOOL, Monochrome,
    OLE_YPOS_CONTAINER, Picture, Font, VgaColor, Checked,
    OLE_YPOS_PIXELS, DISPPROPERTY, IPictureDisp, OLE_XPOS_PIXELS,
    OLE_YSIZE_PIXELS, FONTSTRIKETHROUGH, DISPPARAMS, OLE_OPTEXCLUSIVE,
    FONTNAME, Library, OLE_COLOR, FONTBOLD, dispid, BSTR,
    OLE_XSIZE_PIXELS, VARIANT_BOOL, _check_version
)


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


__all__ = [
    'Gray', 'OLE_TRISTATE', 'FONTITALIC', 'FONTUNDERSCORE',
    'OLE_HANDLE', 'IFontEventsDisp', 'OLE_CANCELBOOL', 'Monochrome',
    'IFont', 'OLE_YPOS_CONTAINER', 'OLE_XPOS_CONTAINER', 'Picture',
    'Font', 'FontEvents', 'VgaColor', 'Default', 'Checked',
    'OLE_YPOS_PIXELS', 'FONTSIZE', 'OLE_XSIZE_CONTAINER', 'IPicture',
    'OLE_YSIZE_CONTAINER', 'StdFont', 'Unchecked', 'Color',
    'IPictureDisp', 'OLE_XPOS_PIXELS', 'OLE_YSIZE_PIXELS',
    'FONTSTRIKETHROUGH', 'OLE_XPOS_HIMETRIC', 'LoadPictureConstants',
    'OLE_YSIZE_HIMETRIC', 'OLE_XSIZE_HIMETRIC', 'OLE_OPTEXCLUSIVE',
    'FONTNAME', 'typelib_path', 'Library', 'OLE_ENABLEDEFAULTBOOL',
    'OLE_COLOR', 'StdPicture', 'OLE_YPOS_HIMETRIC', 'FONTBOLD',
    'OLE_XSIZE_PIXELS', 'IFontDisp'
]

