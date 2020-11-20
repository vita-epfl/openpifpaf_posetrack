import logging
import time

import numpy as np
import openpifpaf

from .. import headmeta, visualizer
from .track_annotation import TrackAnnotation
from .track_base import TrackBase

LOG = logging.getLogger(__name__)


class TrackingPose(TrackBase):
    cache_group = [0, -1]
    forward_tracking_pose = True

    def __init__(self, cif_meta, caf_meta, tcaf_meta, *, pose_generator=None):
        super().__init__()
        self.cif_meta = cif_meta
        self.caf_meta = caf_meta
        self.tcaf_meta = tcaf_meta

        self.n_keypoints = len(cif_meta.keypoints)
        tracking_keypoints = cif_meta.keypoints * len(self.cache_group)
        tracking_sigmas = cif_meta.sigmas * len(self.cache_group)
        tracking_skeleton = (
            self.caf_meta.skeleton
            + [
                (keypoint_i + 1, keypoint_i + 1 + frame_i * self.n_keypoints)
                for frame_i in range(1, len(self.cache_group))
                for keypoint_i in range(self.n_keypoints)
            ]
        )

        self.tracking_cif_meta = openpifpaf.headmeta.Cif(
            'tracking_cif', cif_meta.dataset,
            keypoints=tracking_keypoints,
            sigmas=tracking_sigmas,
            pose=None)
        self.tracking_cif_meta.head_index = 0
        self.tracking_cif_meta.base_stride = cif_meta.base_stride
        self.tracking_cif_meta.upsample_stride = cif_meta.upsample_stride

        self.tracking_caf_meta = openpifpaf.headmeta.Caf(
            'tracking_caf', caf_meta.dataset,
            keypoints=tracking_keypoints,
            sigmas=tracking_sigmas,
            skeleton=tracking_skeleton,
            pose=None)
        self.tracking_caf_meta.head_index = 1
        self.tracking_caf_meta.base_stride = caf_meta.base_stride
        self.tracking_caf_meta.upsample_stride = caf_meta.upsample_stride

        self.pose_generator = pose_generator or openpifpaf.decoder.CifCaf(
            [self.tracking_cif_meta], [self.tracking_caf_meta])
        LOG.debug('keypoint threshold: cifcaf=%f, nms=%f, %s',
                  openpifpaf.decoder.CifCaf.keypoint_threshold,
                  openpifpaf.decoder.utils.nms.Keypoints.keypoint_threshold,
                  openpifpaf.decoder.CifCaf.nms)

        self.vis_multitracking = visualizer.MultiTracking(self.tracking_caf_meta)

    @classmethod
    def cli(cls, parser):
        group = parser.add_argument_group('trackingpose decoder')
        group.add_argument('--show-multitracking', default=False, action='store_true')

    @classmethod
    def configure(cls, args):
        visualizer.MultiTracking.show = args.show_multitracking

    @classmethod
    def factory(cls, head_metas):
        if len(head_metas) < 4:
            return []
        return [
            cls(cif_meta, caf_meta, tcaf_meta)
            for cif_meta, caf_meta, tcaf_meta
            in zip(head_metas, head_metas[1:], head_metas[3:])
            if (isinstance(cif_meta, headmeta.TBaseCif)
                and isinstance(caf_meta, headmeta.TBaseCaf)
                and isinstance(tcaf_meta, headmeta.Tcaf))
        ]

    def soft_nms(self, tracks, frame_number):
        if not tracks:
            return

        # keypoint threshold
        for t in tracks:
            frame_ann = t.pose(self.frame_number)
            if frame_ann is None:
                continue
            kps = frame_ann.data
            kps[kps[:, 2] < openpifpaf.decoder.utils.nms.Keypoints.keypoint_threshold] = 0.0

        occupied = openpifpaf.decoder.utils.Occupancy((
            self.n_keypoints,
            int(max(1, max(np.max(t.frame_pose[-1][1].data[:, 1]) for t in tracks) + 1)),
            int(max(1, max(np.max(t.frame_pose[-1][1].data[:, 0]) for t in tracks) + 1)),
        ), 2, min_scale=4)
        LOG.debug('occupied shape = %s', occupied.occupancy.shape)

        tracks = sorted(tracks, key=lambda tr: -tr.score(frame_number, current_importance=0.01))
        for track in tracks:
            ann = track.pose(frame_number)
            if ann is None:
                continue

            assert ann.joint_scales is not None
            assert len(occupied) == len(ann.data)
            for f, (xyv, joint_s) in enumerate(zip(ann.data, ann.joint_scales)):
                v = xyv[2]
                if v == 0.0:
                    continue

                if occupied.get(f, xyv[0], xyv[1]):
                    xyv[2] = 0.0
                else:
                    occupied.set(f, xyv[0], xyv[1], joint_s)

        # keypoint threshold
        for t in tracks:
            frame_ann = t.pose(self.frame_number)
            if frame_ann is None:
                continue
            kps = frame_ann.data
            kps[kps[:, 2] < openpifpaf.decoder.utils.nms.Keypoints.keypoint_threshold] = 0.0

        if self.pose_generator.occupancy_visualizer is not None:
            LOG.debug('Occupied fields after NMS')
            self.pose_generator.occupancy_visualizer.predicted(occupied)

    def __call__(self, fields, *, initial_annotations=None):
        self.frame_number += 1

        # self.prune_active(frame_number) TODO?

        start = time.perf_counter()

        # initialize tracking poses from self.active tracks
        initial_annotations = []
        for track in self.active:
            tracking_ann = openpifpaf.Annotation(
                self.tracking_cif_meta.keypoints,
                self.tracking_caf_meta.skeleton,
            )
            tracking_ann.id_ = track.id_
            for position_i, frame_i in enumerate(self.cache_group[1:], start=1):
                prev_pose = track.pose(self.frame_number + frame_i)
                if prev_pose is not None:
                    tracking_ann.data[
                        self.n_keypoints * position_i:
                        self.n_keypoints * position_i + self.n_keypoints
                    ] = prev_pose.data
                    tracking_ann.joint_scales[
                        self.n_keypoints * position_i:
                        self.n_keypoints * position_i + self.n_keypoints
                    ] = prev_pose.joint_scales

            tracking_ann.data[tracking_ann.data[:, 2] < 0.05] = 0.0
            if not np.any(tracking_ann.data[:, 2] > 0.0):
                continue
            initial_annotations.append(tracking_ann)

        LOG.debug('using %d initial annotations', len(initial_annotations))

        # use standard pose processor to connect to current frame
        LOG.debug('overwriting CifCaf parameters')
        openpifpaf.decoder.CifCaf.nms = None
        openpifpaf.decoder.CifCaf.keypoint_threshold = 0.001
        tracking_fields = [
            fields[self.cif_meta.head_index],
            np.concatenate([
                fields[self.caf_meta.head_index],
                fields[self.tcaf_meta.head_index],
            ], axis=0)
        ]
        tracking_annotations = self.pose_generator(
            tracking_fields, initial_annotations=initial_annotations)

        # extract new pose annotations from tracking pose
        active_by_id = {t.id_: t for t in self.active}
        for tracking_ann in tracking_annotations:
            single_frame_ann = openpifpaf.Annotation(
                self.cif_meta.keypoints, self.caf_meta.skeleton)
            single_frame_ann.data[:] = tracking_ann.data[:self.n_keypoints]
            single_frame_ann.joint_scales = tracking_ann.joint_scales[:self.n_keypoints]

            track_id = getattr(tracking_ann, 'id_', -1)
            if track_id == -1:
                new_track = TrackAnnotation().add(self.frame_number, single_frame_ann)
                self.active.append(new_track)
                # assign new track id also to tracking pose for visualization
                tracking_ann.id_ = new_track.id_
                continue
            active_by_id[track_id].add(self.frame_number, single_frame_ann)

        # visualize tracking pose with assigned track id
        self.vis_multitracking.predicted(tracking_annotations)

        # nms tracks
        self.soft_nms(self.active, self.frame_number)

        # tag ignore regions
        # if self.gt_anns:  TODO
        #     self.tag_ignore_region(self.frame_number, self.gt_anns)

        # pruning lost tracks
        self.active = [t for t in self.active if self.track_is_viable(t, self.frame_number)]

        LOG.info('active tracks = %d, good = %d, track ids = %s',
                 len(self.active),
                 len([t for t in self.active if self.track_is_good(t, self.frame_number)]),
                 [self.simplified_track_id_map.get(t.id_, t.id_)
                  for t in self.active])
        # if self.track_visualizer or self.track_ann_visualizer:
        #     good_ids = set(t.id_ for t in self.active if self.track_is_good(t, self.frame_number))
        #     good_tracking_anns = [t for t in tracking_annotations
        #                           if getattr(t, 'id_', None) in good_ids]
        #     if self.track_ann_visualizer:
        #         self.track_ann_visualizer.predicted(good_tracking_anns)
        #     if self.track_visualizer:
        #         self.track_visualizer.predicted(
        #             self.frame_number,
        #             [t for t in self.active if t.id_ in good_ids],
        #             self.gt_anns,
        #         )

        LOG.debug('track time: %.3fs', time.perf_counter() - start)
        return self.annotations(self.frame_number)
