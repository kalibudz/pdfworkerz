"""Typed, serializable operations (COR-04). See engine.ops.base.

Importing this package registers every built-in Op, including the text
Ops in engine.ops.text (EDT-01..EDT-04, EDT-06, EDT-07, FNT-11) and the link Ops in
engine.ops.links (EDT-10) and the image Ops in engine.ops.images (EDT-08) and
the shape Ops in engine.ops.shapes (EDT-09) and the spell-check Ops
in engine.ops.spellcheck (EDT-11) and the text-unit query in engine.ops.units (EDT-16) -- importing
engine.ops.base alone would not run their @register_op decorators.
"""

from __future__ import annotations

from engine.ops.annotations import (
    AddAnnotationShapeOp,
    AddCommentOp,
    AddNoteOp,
    AnnotationSummaryOp,
    DeleteAnnotationOp,
    FlattenAnnotationsOp,
    MarkTextOp,
    PageAnnotationsOp,
    UpdateAnnotationOp,
)
from engine.ops.base import InspectOp, Op, RenderPageOp, op_registry, parse_op
from engine.ops.batch import BatchOp
from engine.ops.certs import GenerateCertificateOp
from engine.ops.design import BackgroundOp, BatesOp, HeaderFooterOp, PageNumbersOp, StampOp, WatermarkOp
from engine.ops.forms import (
    CreateFieldOp,
    DeleteFieldOp,
    DetectXFAOp,
    EditFieldOp,
    ExportFormDataOp,
    FillFieldsOp,
    FlattenFormOp,
    ImportFormDataOp,
    PageFieldsOp,
    SetTabOrderOp,
)
from engine.ops.images import (
    CropImageOp,
    DeleteImageOp,
    InsertImageOp,
    MoveImageOp,
    PageImagesOp,
    ReplaceImageOp,
)
from engine.ops.layout import BookletOp, CropPagesOp, NUpOp, ResizePagesOp, UncropPagesOp
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp, UpdateLinkOp
from engine.ops.objects import DeleteObjectsOp, DuplicateObjectsOp, MoveObjectsOp, PageBlocksOp
from engine.ops.pages import (
    DeletePagesOp,
    DuplicatePagesOp,
    ExtractPagesOp,
    FindBlankPagesOp,
    InsertPagesOp,
    MergeOp,
    MovePagesOp,
    RemoveBlankPagesOp,
    ReorderPagesOp,
    RotatePagesOp,
    SplitOp,
)
from engine.ops.protect import RemovePasswordOp, SetPasswordOp, SetPermissionsOp
from engine.ops.redact import FindRedactionCandidatesOp, RedactAreasOp, SanitizeOp
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp
from engine.ops.sign import DocumentSignatureStatusOp, SignDocumentOp, ValidateSignaturesOp
from engine.ops.signatures import PlaceSignatureOp
from engine.ops.spellcheck import CorrectWordOp, SpellCheckOp
from engine.ops.structure import (
    AddBookmarkOp,
    AttachFileOp,
    AutoBookmarksOp,
    ContentsPageOp,
    DeleteBookmarkOp,
    ExtractAttachmentOp,
    GetMetadataOp,
    ListAttachmentsOp,
    ListBookmarksOp,
    ListLayersOp,
    MoveBookmarkOp,
    RemoveAttachmentOp,
    SetBookmarksOp,
    SetLayerVisibilityOp,
    SetMetadataOp,
    SetPageLabelsOp,
    UpdateBookmarkOp,
)
from engine.ops.text import (
    DeleteTextOp,
    EditSpanOp,
    InsertTextOp,
    ReflowTextOp,
    ReplaceTextOp,
    RestyleSpanOp,
    RestyleTextOp,
)
from engine.ops.units import PageTextUnitsOp

__all__ = [
    "AddAnnotationShapeOp",
    "AddBookmarkOp",
    "AddCommentOp",
    "AddLinkOp",
    "AddNoteOp",
    "AnnotationSummaryOp",
    "AttachFileOp",
    "AutoBookmarksOp",
    "BackgroundOp",
    "BatchOp",
    "BatesOp",
    "BookletOp",
    "ContentsPageOp",
    "CorrectWordOp",
    "CreateFieldOp",
    "CropImageOp",
    "CropPagesOp",
    "DeleteAnnotationOp",
    "DeleteBookmarkOp",
    "DeleteFieldOp",
    "DeleteImageOp",
    "DeleteObjectsOp",
    "DeletePagesOp",
    "DeleteShapeOp",
    "DeleteTextOp",
    "DetectXFAOp",
    "DocumentSignatureStatusOp",
    "DrawShapeOp",
    "DuplicateObjectsOp",
    "DuplicatePagesOp",
    "EditFieldOp",
    "EditShapeOp",
    "EditSpanOp",
    "ExportFormDataOp",
    "ExtractAttachmentOp",
    "ExtractPagesOp",
    "FillFieldsOp",
    "FindBlankPagesOp",
    "FindRedactionCandidatesOp",
    "FlattenAnnotationsOp",
    "FlattenFormOp",
    "GenerateCertificateOp",
    "GetMetadataOp",
    "HeaderFooterOp",
    "ImportFormDataOp",
    "InsertImageOp",
    "InsertPagesOp",
    "InsertTextOp",
    "InspectOp",
    "ListAttachmentsOp",
    "ListBookmarksOp",
    "ListLayersOp",
    "MarkTextOp",
    "MergeOp",
    "MoveBookmarkOp",
    "MoveImageOp",
    "MoveObjectsOp",
    "MovePagesOp",
    "NUpOp",
    "Op",
    "PageAnnotationsOp",
    "PageBlocksOp",
    "PageFieldsOp",
    "PageImagesOp",
    "PageLinksOp",
    "PageNumbersOp",
    "PageShapesOp",
    "PageTextUnitsOp",
    "PlaceSignatureOp",
    "RedactAreasOp",
    "ReflowTextOp",
    "RemoveAttachmentOp",
    "RemoveBlankPagesOp",
    "RemoveLinkOp",
    "RemovePasswordOp",
    "RenderPageOp",
    "ReorderPagesOp",
    "ReplaceImageOp",
    "ReplaceTextOp",
    "ResizePagesOp",
    "RestyleSpanOp",
    "RestyleTextOp",
    "RotatePagesOp",
    "SanitizeOp",
    "SetBookmarksOp",
    "SetLayerVisibilityOp",
    "SetMetadataOp",
    "SetPageLabelsOp",
    "SetPasswordOp",
    "SetPermissionsOp",
    "SetTabOrderOp",
    "SignDocumentOp",
    "SpellCheckOp",
    "SplitOp",
    "StampOp",
    "UncropPagesOp",
    "UpdateAnnotationOp",
    "UpdateBookmarkOp",
    "UpdateLinkOp",
    "ValidateSignaturesOp",
    "WatermarkOp",
    "op_registry",
    "parse_op",
]
