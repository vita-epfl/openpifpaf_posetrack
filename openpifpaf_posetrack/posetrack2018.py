import argparse

import torch

import openpifpaf

from . import collate, datasets, encoder, headmeta, metric, transforms
from .signal import Signal
from .transforms import SingleImage as S

from .constants import (
    KEYPOINTS,
    SIGMAS,
    UPRIGHT_POSE,
    SKELETON,
    DENSER_CONNECTIONS,
)


class Posetrack2018(openpifpaf.datasets.DataModule):
    # cli configurable
    train_annotations = 'data-posetrack2018/annotations/train/*.json'
    val_annotations = 'data-posetrack2018/annotations/val/*.json'
    eval_annotations = val_annotations
    data_root = 'data-posetrack2018'

    square_edge = 385
    augmentation = True
    rescale_images = 1.0
    upsample_stride = 1
    min_kp_anns = 1
    bmin = 0.1
    sample_pairing = False

    eval_long_edge = 801
    eval_orientation_invariant = 0.0
    eval_extended_scale = False

    ablation_without_tcaf = False

    def __init__(self):
        super().__init__()

        cif = headmeta.TBaseCif('cif', 'posetrack2018',
                                keypoints=KEYPOINTS,
                                sigmas=SIGMAS,
                                pose=UPRIGHT_POSE,
                                draw_skeleton=SKELETON)
        caf = headmeta.TBaseCaf('caf', 'posetrack2018',
                                keypoints=KEYPOINTS,
                                sigmas=SIGMAS,
                                pose=UPRIGHT_POSE,
                                skeleton=SKELETON)
        dcaf = headmeta.TBaseCaf('dcaf', 'posetrack2018',
                                 keypoints=KEYPOINTS,
                                 sigmas=SIGMAS,
                                 pose=UPRIGHT_POSE,
                                 skeleton=DENSER_CONNECTIONS,
                                 sparse_skeleton=SKELETON,
                                 only_in_field_of_view=True)
        tcaf = headmeta.Tcaf('tcaf', 'posetrack2018',
                             keypoints_single_frame=KEYPOINTS,
                             sigmas_single_frame=SIGMAS,
                             pose_single_frame=UPRIGHT_POSE,
                             draw_skeleton_single_frame=SKELETON,
                             only_in_field_of_view=True)

        cif.upsample_stride = self.upsample_stride
        caf.upsample_stride = self.upsample_stride
        dcaf.upsample_stride = self.upsample_stride
        tcaf.upsample_stride = self.upsample_stride
        self.head_metas = [cif, caf, dcaf, tcaf]

        if self.ablation_without_tcaf:
            self.head_metas = [cif, caf, dcaf]

    @classmethod
    def cli(cls, parser: argparse.ArgumentParser):
        group2018 = parser.add_argument_group('data module Posetrack2018')
        group2018.add_argument('--posetrack2018-train-annotations',
                               default=cls.train_annotations,
                               help='train annotations')
        group2018.add_argument('--posetrack2018-val-annotations',
                               default=cls.val_annotations,
                               help='val annotations')
        group2018.add_argument('--posetrack2018-eval-annotations',
                               default=cls.eval_annotations,
                               help='eval annotations')
        group2018.add_argument('--posetrack2018-data-root',
                               default=cls.data_root,
                               help='data root')

        group = parser.add_argument_group('data module Posetrack')
        group.add_argument('--posetrack-square-edge',
                           default=cls.square_edge, type=int,
                           help='square edge of input images')
        assert cls.augmentation
        group.add_argument('--posetrack-no-augmentation',
                           dest='posetrack_augmentation',
                           default=True, action='store_false',
                           help='do not apply data augmentation')
        group.add_argument('--posetrack-rescale-images',
                           default=cls.rescale_images, type=float,
                           help='overall rescale factor for images')
        group.add_argument('--posetrack-upsample',
                           default=cls.upsample_stride, type=int,
                           help='head upsample stride')
        group.add_argument('--posetrack-min-kp-anns',
                           default=cls.min_kp_anns, type=int,
                           help='filter images with fewer keypoint annotations')
        group.add_argument('--posetrack-bmin', default=cls.bmin, type=float)
        group.add_argument('--posetrack-sample-pairing',
                           default=False, action='store_true')

        group.add_argument('--posetrack-eval-long-edge', default=cls.eval_long_edge, type=int)
        assert not cls.eval_extended_scale
        group.add_argument('--posetrack-eval-extended-scale', default=False, action='store_true')
        group.add_argument('--posetrack-eval-orientation-invariant',
                           default=cls.eval_orientation_invariant, type=float)

        group.add_argument('--ablation-without-tcaf', default=False, action='store_true')

    @classmethod
    def configure(cls, args: argparse.Namespace):
        # extract global information
        cls.debug = args.debug
        cls.pin_memory = args.pin_memory

        # posetrack2018 specific
        cls.train_annotations = args.posetrack2018_train_annotations
        cls.val_annotations = args.posetrack2018_val_annotations
        cls.eval_annotations = args.posetrack2018_eval_annotations
        cls.data_root = args.posetrack2018_data_root

        # common posetrack
        cls.square_edge = args.posetrack_square_edge
        cls.augmentation = args.posetrack_augmentation
        cls.rescale_images = args.posetrack_rescale_images
        cls.upsample_stride = args.posetrack_upsample
        cls.min_kp_anns = args.posetrack_min_kp_anns
        cls.bmin = args.posetrack_bmin
        cls.sample_pairing = args.posetrack_sample_pairing

        # evaluation
        cls.eval_long_edge = args.posetrack_eval_long_edge
        cls.eval_orientation_invariant = args.posetrack_eval_orientation_invariant
        cls.eval_extended_scale = args.posetrack_eval_extended_scale

        # ablation
        cls.ablation_without_tcaf = args.ablation_without_tcaf

    def _preprocess(self):
        encoders = [
            encoder.SingleImage(openpifpaf.encoder.Cif(self.head_metas[0], bmin=self.bmin)),
            encoder.SingleImage(openpifpaf.encoder.Caf(self.head_metas[1], bmin=self.bmin)),
            encoder.SingleImage(openpifpaf.encoder.Caf(self.head_metas[2], bmin=self.bmin)),
        ]
        if not self.ablation_without_tcaf:
            encoders.append(encoder.Tcaf(self.head_metas[3], bmin=self.bmin))

        return openpifpaf.transforms.Compose([
            *self.common_preprocess(),
            transforms.Encoders(encoders),
        ])

    @classmethod
    def common_preprocess(cls):
        if not cls.augmentation:
            return [
                openpifpaf.transforms.NormalizeAnnotations(),
                openpifpaf.transforms.RescaleAbsolute(cls.square_edge),
                openpifpaf.transforms.CenterPad(cls.square_edge),
                openpifpaf.transforms.EVAL_TRANSFORM,
            ]

        sample_pairing_t = None
        if cls.sample_pairing:
            sample_pairing_t = transforms.SamplePairing()

        hflip_posetrack = openpifpaf.transforms.HFlip(
            KEYPOINTS,
            openpifpaf.plugins.coco.constants.HFLIP)
        return [
            S(transforms.NormalizePosetrack()),
            openpifpaf.transforms.RandomApply(transforms.RandomizeOneFrame(), 0.2),
            S(transforms.AddCrowdForIncompleteHead()),
            S(openpifpaf.transforms.RandomApply(hflip_posetrack, 0.5)),
            S(openpifpaf.transforms.RescaleRelative(
                (0.5, 2.0), power_law=True, absolute_reference=801, stretch_range=(0.75, 1.33))),
            S(openpifpaf.transforms.RandomChoice(
                [openpifpaf.transforms.RotateBy90(angle_perturbation=30.0),
                 openpifpaf.transforms.RotateUniform(30.0)],
                [0.25],
            )),
            transforms.Crop(cls.square_edge, max_shift=30.0),
            transforms.Pad(cls.square_edge, max_shift=30.0),
            sample_pairing_t,
            S(openpifpaf.transforms.TRAIN_TRANSFORM),
        ]

    def train_loader(self):
        train_data = datasets.Posetrack2018(
            annotation_files=self.train_annotations,
            data_root=self.data_root,
            group=[(0, -12), (0, -8), (0, -4)],
            preprocess=self._preprocess(),
            only_annotated=True,
        )

        # to keep base-net batch size equal across batches, train tracking with
        # half the batch-size of single-image datasets
        assert self.batch_size % 2 == 0
        return torch.utils.data.DataLoader(
            train_data, batch_size=self.batch_size // 2, shuffle=not self.debug,
            pin_memory=self.pin_memory, num_workers=self.loader_workers, drop_last=True,
            collate_fn=collate.collate_tracking_images_targets_meta)

    def val_loader(self):
        val_data = datasets.Posetrack2018(
            annotation_files=self.val_annotations,
            data_root=self.data_root,
            group=[(0, -12), (0, -8), (0, -4)],
            preprocess=self._preprocess(),
            only_annotated=True,
        )

        # to keep base-net batch size equal across batches, train tracking with
        # half the batch-size of single-image datasets
        assert self.batch_size % 2 == 0
        return torch.utils.data.DataLoader(
            val_data, batch_size=self.batch_size // 2, shuffle=not self.debug,
            pin_memory=self.pin_memory, num_workers=self.loader_workers, drop_last=True,
            collate_fn=collate.collate_tracking_images_targets_meta)

    @classmethod
    def common_eval_preprocess(cls):
        rescale_t = None
        if cls.eval_extended_scale:
            assert cls.eval_long_edge
            rescale_t = [
                openpifpaf.transforms.DeterministicEqualChoice([
                    openpifpaf.transforms.RescaleAbsolute(cls.eval_long_edge),
                    openpifpaf.transforms.RescaleAbsolute((cls.eval_long_edge - 1) // 2 + 1),
                ], salt=1)
            ]
        elif cls.eval_long_edge:
            rescale_t = openpifpaf.transforms.RescaleAbsolute(cls.eval_long_edge)

        if cls.batch_size == 1:
            padding_t = openpifpaf.transforms.CenterPadTight(16)
        else:
            assert cls.eval_long_edge
            padding_t = openpifpaf.transforms.CenterPad(cls.eval_long_edge)

        orientation_t = None
        if cls.eval_orientation_invariant:
            orientation_t = openpifpaf.transforms.DeterministicEqualChoice([
                None,
                openpifpaf.transforms.RotateBy90(fixed_angle=90),
                openpifpaf.transforms.RotateBy90(fixed_angle=180),
                openpifpaf.transforms.RotateBy90(fixed_angle=270),
            ], salt=3)

        return [
            transforms.Ungroup(),
            transforms.NormalizePosetrack(),
            rescale_t,
            padding_t,
            orientation_t,
        ]

    def _eval_preprocess(self):
        return openpifpaf.transforms.Compose([
            *self.common_eval_preprocess(),
            openpifpaf.transforms.ToAnnotations([
                openpifpaf.transforms.ToKpAnnotations(
                    ['person'],
                    keypoints_by_category={1: self.head_metas[0].keypoints},
                    skeleton_by_category={1: self.head_metas[1].skeleton},
                ),
                openpifpaf.transforms.ToCrowdAnnotations(['person']),
            ]),
            openpifpaf.transforms.EVAL_TRANSFORM,
        ])

    def eval_loader(self):
        eval_data = datasets.Posetrack2018(
            annotation_files=self.eval_annotations,
            data_root=self.data_root,
            preprocess=self._eval_preprocess(),
        )
        eval_loader = torch.utils.data.DataLoader(
            eval_data, batch_size=self.batch_size, shuffle=False,
            pin_memory=self.pin_memory, num_workers=self.loader_workers, drop_last=False,
            collate_fn=openpifpaf.datasets.collate_images_anns_meta)
        return LoaderWithReset(eval_loader, 'annotation_file')

    def metrics(self):
        eval_data = datasets.Posetrack2018(
            annotation_files=self.eval_annotations,
            data_root=self.data_root,
            preprocess=self._eval_preprocess(),
        )
        return [metric.Posetrack(
            images=eval_data.meta_images(),
            categories=eval_data.meta_categories(),
            ground_truth=self.eval_annotations,
        )]


class LoaderWithReset:
    def __init__(self, parent, key_to_monitor):
        self.parent = parent
        self.key_to_monitor = key_to_monitor

        self.previous_value = None

    def __iter__(self):
        for images, anns, metas in self.parent:
            value = metas[0][self.key_to_monitor]
            if len(metas) >= 2:
                assert all(m[self.key_to_monitor] == value for m in metas[1:])

            if value != self.previous_value:
                Signal.emit('eval_reset')
                self.previous_value = value

            yield images, anns, metas

    def __len__(self):
        return len(self.parent)
