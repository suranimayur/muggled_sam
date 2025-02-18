#!/usr/bin/env python3
# -*- coding: utf-8 -*-


# ---------------------------------------------------------------------------------------------------------------------
# %% Imports

import cv2
import numpy as np


# ---------------------------------------------------------------------------------------------------------------------
# %% Classes


class CheckerPattern:
    """
    Class used to draw a checker pattern as the background of images
    that are either masked or have transparency (i.e. alpha channels)
    """

    # .................................................................................................................

    def __init__(self, checker_size_px=64, brightness_pct=75, contrast_pct=35, flipped=False):

        # Force percent values to be 0-to-100
        brightness_pct = min(abs(brightness_pct), 100)
        contrast_pct = min(abs(contrast_pct), 100)

        # Figure out the tile brightness (uint8) values
        mid_color_uint8 = 255 * brightness_pct / 100
        max_diff_uint8 = min(255 - mid_color_uint8, mid_color_uint8)
        real_diff_uint8 = max_diff_uint8 * contrast_pct / 100
        color_a = round(max(min(mid_color_uint8 - real_diff_uint8, 255), 0))
        color_b = round(max(min(mid_color_uint8 + real_diff_uint8, 255), 0))
        if flipped:
            color_a, color_b = color_b, color_a

        # Create the base pattern
        base_wh = (checker_size_px, checker_size_px)
        base_pattern = np.uint8(((color_a, color_b), (color_b, color_a)))
        base_pattern = cv2.resize(base_pattern, dsize=base_wh, interpolation=cv2.INTER_NEAREST_EXACT)
        self._base = base_pattern
        self._full_pattern = cv2.cvtColor(self._base.copy(), cv2.COLOR_GRAY2BGR)

    # .................................................................................................................

    def __repr__(self):
        name = self.__class__.__name__
        color_a = self._base[0, 0]
        color_b = self._base[0, -1]
        return f"{name} ({color_a} | {color_b})"

    # .................................................................................................................

    def draw_like(self, other_frame):
        """Draw a full checker pattern matching the shape of the given 'other_frame'"""
        other_h, other_w = other_frame.shape[0:2]
        return self.draw(other_h, other_w)

    # .................................................................................................................

    def draw(self, frame_h, frame_w):
        """Draw a full checker pattern of the given size"""

        # Re-draw the full pattern if the render size doesn't match
        curr_h, curr_w = self._full_pattern.shape[0:2]
        if curr_h != frame_h or curr_w != frame_w:

            # Figure out how much to pad to fit target shape
            base_h, base_w = self._base.shape[0:2]
            x_pad = max(frame_w - base_w, 0)
            y_pad = max(frame_h - base_h, 0)

            # Make fully sized pattern but duplicating the base pattern
            l, t = x_pad // 2, y_pad // 2
            r, b = x_pad - l, y_pad - t
            pattern = cv2.copyMakeBorder(self._base, t, b, l, r, cv2.BORDER_WRAP)

            # Funky sanity check, in case the given frame sizing is smaller than our base pattern!
            pattern = pattern[0:frame_h, 0:frame_w]
            self._full_pattern = cv2.cvtColor(pattern, cv2.COLOR_GRAY2BGR)

        return self._full_pattern

    # .................................................................................................................

    def superimpose(self, other_frame, mask):
        """Draw the given frame with a checker pattern based on the provided mask"""

        # Create checker pattern matched to other frame
        checker_pattern = self.draw_like(other_frame).copy()

        # Make sure the mask is matched to the other frame size
        frame_h, frame_w = other_frame.shape[0:2]
        is_same_size = mask.shape[0] == frame_h and mask.shape[1] == frame_w
        if not is_same_size:
            mask = cv2.resize(mask, dsize=(frame_w, frame_h), interpolation=cv2.INTER_NEAREST_EXACT)

        # Force mask to be 3-channels, if it isn't already
        if not mask.ndim == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

        # Combine masked frame with (inverted) masked checker pattern to form output
        inv_mask = cv2.bitwise_not(mask)
        checker_masked = cv2.bitwise_and(checker_pattern, inv_mask)
        other_masked = cv2.bitwise_and(other_frame, mask)
        return cv2.bitwise_or(checker_masked, other_masked)

    # .................................................................................................................


# ---------------------------------------------------------------------------------------------------------------------
# %% Functions


def blank_image(height: int, width: int, bgr_color: None | int | tuple[int, int, int] = None):
    """Helper used to create a blank image of a given size (and optionally provide a fill color)"""

    # If no color is given, default to zeros
    if bgr_color is None:
        return np.zeros((height, width, 3), dtype=np.uint8)

    # If only 1 number is given for the color, duplicate it to form a gray value
    if isinstance(bgr_color, int):
        bgr_color = (bgr_color, bgr_color, bgr_color)

    return np.full((height, width, 3), bgr_color, dtype=np.uint8)


def blank_mask(height: int, width: int, gray_value: int = 0):
    """Helper used to create a blank mask (i.e. grayscale/no channels) of a given size"""
    return np.full((height, width), gray_value, dtype=np.uint8)


def draw_box_outline(frame, color=(0, 0, 0), thickness=1):
    """Helper used to draw a box outline around the outside of a given frame"""
    img_h, img_w = frame.shape[0:2]
    x1, y1 = thickness - 1, thickness - 1
    x2, y2 = img_w - thickness, img_h - thickness
    return cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness, cv2.LINE_4)


def draw_normalized_polygons(
    frame, polygon_xy_norm_list, color=(0, 255, 255), thickness=1, bg_color=None, line_type=cv2.LINE_AA, is_closed=True
):

    # Make sure we have a list of polygons
    if not isinstance(polygon_xy_norm_list, (list, tuple)):
        polygon_xy_norm_list = tuple(polygon_xy_norm_list)

    # Convert normalize xy into pixel units
    frame_h, frame_w = frame.shape[0:2]
    norm_to_px_scale = np.float32((frame_w - 1, frame_h - 1))
    xy_px_list = [np.int32(poly * norm_to_px_scale) for poly in polygon_xy_norm_list]

    # Draw polygon with background if needed
    if bg_color is not None:
        bg_thick = thickness + 1
        cv2.polylines(frame, xy_px_list, is_closed, bg_color, bg_thick, line_type)
    return cv2.polylines(frame, xy_px_list, is_closed, color, thickness, line_type)


def convert_color(color, conversion_code):
    """
    Helper used to convert singular color values, without requiring a full image
    For example:
        bgr_color = (12, 23, 34)
        hsv_color = convert_color(bgr_color, cv2.BGR2HSV_FULL)
        -> hsv_color = (21, 165, 34)
    """

    color_as_img = np.expand_dims(np.uint8(color), (0, 1))
    converted_color_as_img = cv2.cvtColor(color_as_img, conversion_code)

    return tuple(converted_color_as_img.squeeze().tolist())


def linear_gradient_image(h, w, start_color=(0, 0, 0), end_color=(255, 255, 255), vertical=True):
    """
    Helper used to make simple linear gradient images, either vertical or horizontal
    Does not (currently) support angled gradients!
    """

    weight = np.linspace(0, 1, h if vertical else w, dtype=np.float32)
    weight = np.expand_dims(weight, axis=(1, 2) if vertical else (0, 2))
    col_1px = (1.0 - weight) * np.float32(start_color) + weight * np.float32(end_color)

    return cv2.resize(np.uint8(col_1px), dsize=(w, h), interpolation=cv2.INTER_NEAREST_EXACT)


def get_image_hw_to_fill(image, target_hw) -> tuple[int, int]:
    """
    Helper used to find the sizing (height & width) of a given image
    if it is scaled to fit in the target height & width, assuming the
    aspect ratio of the image is preserved.
    For example, to fit a 100x200 image into a 600x600 square space,
    while preserving aspect ratio, the image would be scaled to 300x600

    Returns:
        output_height, output_width
    """

    targ_h, targ_w = target_hw
    img_h, img_w = image.shape[0:2]

    scale = min(targ_h / img_h, targ_w / img_w)
    out_h = round(scale * img_h)
    out_w = round(scale * img_w)

    return out_h, out_w


def get_image_hw_for_max_height(image, max_height_px=800):

    img_h, img_w = image.shape[0:2]
    scale = max_height_px / img_h
    out_h = round(scale * img_h)
    out_w = round(scale * img_w)

    return out_h, out_w


def get_image_hw_for_max_width(image, max_width_px=800):

    img_h, img_w = image.shape[0:2]
    scale = max_width_px / img_w
    out_h = round(scale * img_h)
    out_w = round(scale * img_w)

    return out_h, out_w


def get_image_hw_for_max_side_length(image, max_side_length=800):

    img_h, img_w = image.shape[0:2]
    scale = min(max_side_length / img_h, max_side_length / img_h)
    out_h = round(scale * img_h)
    out_w = round(scale * img_w)

    return out_h, out_w


if __name__ == "__main__":

    # w = 400
    # h = 200
    # start_color = (0, 0, 0)
    # end_color = (0, 0, 255)
    # vertical = False

    # weight = np.linspace(0, 1, h if vertical else w, dtype=np.float32)
    # weight = np.expand_dims(weight, axis=(1, 2) if vertical else (0, 2))
    # col_1px = (1.0 - weight) * np.float32(start_color) + weight * np.float32(end_color)

    # lingrad = cv2.resize(np.uint8(col_1px), dsize=(w, h), interpolation=cv2.INTER_NEAREST_EXACT)

    lingrad = linear_gradient_image(200, 400, (255, 0, 0), (0, 255, 255))
    cv2.imshow("Gradient", lingrad)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
