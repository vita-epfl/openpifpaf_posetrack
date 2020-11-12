import argparse

import torch

import openpifpaf
from openpifpaf.datasets.constants import (
    COCO_CATEGORIES,
    COCO_KEYPOINTS,
    COCO_PERSON_SKELETON,
    COCO_PERSON_SIGMAS,
    COCO_PERSON_SCORE_WEIGHTS,
    COCO_UPRIGHT_POSE,
    DENSER_COCO_PERSON_CONNECTIONS,
    HFLIP,
)
from . import collate, encoder, headmeta, transforms
from .transforms import SingleImage as S

try:
    import pycocotools.coco
    # monkey patch for Python 3 compat
    pycocotools.coco.unicode = str
except ImportError:
    pass


class CocoKpSt(openpifpaf.datasets.DataModule):
    def __init__(self):
        super().__init__()

        cif = headmeta.TBaseCif(
            'cif', 'cocokpst',
            keypoints=COCO_KEYPOINTS,
            sigmas=COCO_PERSON_SIGMAS,
            pose=COCO_UPRIGHT_POSE,
            draw_skeleton=COCO_PERSON_SKELETON,
            score_weights=COCO_PERSON_SCORE_WEIGHTS,
        )
        caf = headmeta.TBaseCaf(
            'caf', 'cocokpst',
            keypoints=COCO_KEYPOINTS,
            sigmas=COCO_PERSON_SIGMAS,
            pose=COCO_UPRIGHT_POSE,
            skeleton=COCO_PERSON_SKELETON,
        )
        dcaf = headmeta.TBaseCaf(
            'caf25', 'cocokpst',
            keypoints=COCO_KEYPOINTS,
            sigmas=COCO_PERSON_SIGMAS,
            pose=COCO_UPRIGHT_POSE,
            skeleton=DENSER_COCO_PERSON_CONNECTIONS,
            sparse_skeleton=COCO_PERSON_SKELETON,
            only_in_field_of_view=True,
        )
        tcaf = headmeta.Tcaf(
            'tcaf', 'cocokpst',
            keypoints=COCO_KEYPOINTS,
            sigmas=COCO_PERSON_SIGMAS,
            pose=COCO_UPRIGHT_POSE,
            draw_skeleton=COCO_PERSON_SKELETON,
            only_in_field_of_view=True,
        )

        cif.upsample_stride = openpifpaf.datasets.CocoKp.upsample_stride
        caf.upsample_stride = openpifpaf.datasets.CocoKp.upsample_stride
        dcaf.upsample_stride = openpifpaf.datasets.CocoKp.upsample_stride
        tcaf.upsample_stride = openpifpaf.datasets.CocoKp.upsample_stride
        self.head_metas = [cif, caf, dcaf, tcaf]

    @classmethod
    def cli(cls, parser: argparse.ArgumentParser):
        # group = parser.add_argument_group('data module CocoKpSt')
        pass

    @classmethod
    def configure(cls, args: argparse.Namespace):
        # extract global information
        pass

    def _preprocess(self):
        bmin = openpifpaf.datasets.CocoKp.bmin
        encoders = (
            encoder.SingleImage(openpifpaf.encoder.Cif(self.head_metas[0], bmin=bmin)),
            encoder.SingleImage(openpifpaf.encoder.Caf(self.head_metas[1], bmin=bmin)),
            encoder.SingleImage(openpifpaf.encoder.Caf(self.head_metas[2], bmin=bmin)),
            encoder.Tcaf(self.head_metas[3], bmin=bmin),
        )

        if not openpifpaf.datasets.CocoKp.augmentation:
            return openpifpaf.transforms.Compose([
                openpifpaf.transforms.NormalizeAnnotations(),
                openpifpaf.transforms.RescaleAbsolute(openpifpaf.datasets.CocoKp.square_edge),
                openpifpaf.transforms.CenterPad(openpifpaf.datasets.CocoKp.square_edge),
                transforms.ImageToTracking(),
                S(openpifpaf.transforms.EVAL_TRANSFORM),
                transforms.Encoders(encoders),
            ])

        if openpifpaf.datasets.CocoKp.extended_scale:
            rescale_t = openpifpaf.transforms.RescaleRelative(
                scale_range=(0.25 * openpifpaf.datasets.CocoKp.rescale_images,
                             2.0 * openpifpaf.datasets.CocoKp.rescale_images),
                power_law=True, stretch_range=(0.75, 1.33))
        else:
            rescale_t = openpifpaf.transforms.RescaleRelative(
                scale_range=(0.4 * openpifpaf.datasets.CocoKp.rescale_images,
                             2.0 * openpifpaf.datasets.CocoKp.rescale_images),
                power_law=True, stretch_range=(0.75, 1.33))

        return openpifpaf.transforms.Compose([
            openpifpaf.transforms.NormalizeAnnotations(),
            openpifpaf.transforms.RandomApply(
                openpifpaf.transforms.HFlip(COCO_KEYPOINTS, HFLIP), 0.5),
            rescale_t,
            openpifpaf.transforms.RandomApply(openpifpaf.transforms.Blur(),
                                              openpifpaf.datasets.CocoKp.blur),
            transforms.ImageToTracking(),
            transforms.Crop(openpifpaf.datasets.CocoKp.square_edge, max_shift=30.0),
            transforms.Pad(openpifpaf.datasets.CocoKp.square_edge, max_shift=30.0),
            S(openpifpaf.transforms.RandomApply(openpifpaf.transforms.RotateBy90(),
                                                openpifpaf.datasets.CocoKp.orientation_invariant)),
            S(openpifpaf.transforms.TRAIN_TRANSFORM),
            transforms.Encoders(encoders),
        ])

    def train_loader(self):
        train_data = openpifpaf.datasets.Coco(
            image_dir=openpifpaf.datasets.CocoKp.train_image_dir,
            ann_file=openpifpaf.datasets.CocoKp.train_annotations,
            preprocess=self._preprocess(),
            annotation_filter=True,
            min_kp_anns=openpifpaf.datasets.CocoKp.min_kp_anns,
            category_ids=[1],
        )
        return torch.utils.data.DataLoader(
            train_data,
            batch_size=openpifpaf.datasets.CocoKp.batch_size // 2,
            shuffle=(not openpifpaf.datasets.CocoKp.debug
                     and openpifpaf.datasets.CocoKp.augmentation),
            pin_memory=openpifpaf.datasets.CocoKp.pin_memory,
            num_workers=openpifpaf.datasets.CocoKp.loader_workers,
            drop_last=True,
            collate_fn=collate.collate_tracking_images_targets_meta,
        )

    def val_loader(self):
        val_data = openpifpaf.datasets.Coco(
            image_dir=openpifpaf.datasets.CocoKp.val_image_dir,
            ann_file=openpifpaf.datasets.CocoKp.val_annotations,
            preprocess=self._preprocess(),
            annotation_filter=True,
            min_kp_anns=openpifpaf.datasets.CocoKp.min_kp_anns,
            category_ids=[1],
        )
        return torch.utils.data.DataLoader(
            val_data,
            batch_size=openpifpaf.datasets.CocoKp.batch_size // 2,
            shuffle=False,
            pin_memory=openpifpaf.datasets.CocoKp.pin_memory,
            num_workers=openpifpaf.datasets.CocoKp.loader_workers,
            drop_last=True,
            collate_fn=collate.collate_tracking_images_targets_meta,
        )

    def _eval_preprocess(self):
        return openpifpaf.transforms.Compose([
            *openpifpaf.datasets.CocoKp.common_eval_preprocess(),
            openpifpaf.transforms.ToAnnotations([
                openpifpaf.transforms.ToKpAnnotations(
                    COCO_CATEGORIES,
                    keypoints_by_category={1: self.head_metas[0].keypoints},
                    skeleton_by_category={1: self.head_metas[1].skeleton},
                ),
                openpifpaf.transforms.ToCrowdAnnotations(COCO_CATEGORIES),
            ]),
            openpifpaf.transforms.EVAL_TRANSFORM,
        ])

    def eval_loader(self):
        eval_data = openpifpaf.datasets.Coco(
            image_dir=openpifpaf.datasets.CocoKp.eval_image_dir,
            ann_file=openpifpaf.datasets.CocoKp.eval_annotations,
            preprocess=self._eval_preprocess(),
            annotation_filter=openpifpaf.datasets.CocoKp.eval_annotation_filter,
            min_kp_anns=(openpifpaf.datasets.CocoKp.min_kp_anns
                         if openpifpaf.datasets.CocoKp.eval_annotation_filter
                         else 0),
            category_ids=[1] if openpifpaf.datasets.CocoKp.eval_annotation_filter else [],
        )
        return torch.utils.data.DataLoader(
            eval_data,
            batch_size=openpifpaf.datasets.CocoKp.batch_size,
            shuffle=False,
            pin_memory=openpifpaf.datasets.CocoKp.pin_memory,
            num_workers=openpifpaf.datasets.CocoKp.loader_workers,
            drop_last=False,
            collate_fn=openpifpaf.datasets.collate_images_anns_meta,
        )

    def metrics(self):
        return [openpifpaf.metric.Coco(
            pycocotools.coco.COCO(openpifpaf.datasets.CocoKp.eval_annotations),
            max_per_image=20,
            category_ids=[1],
            iou_type='keypoints',
        )]
