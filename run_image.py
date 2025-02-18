#!/usr/bin/env python3
# -*- coding: utf-8 -*-


# ---------------------------------------------------------------------------------------------------------------------
# %% Imports

import argparse
import os.path as osp
from time import perf_counter

import torch
import cv2
import numpy as np

from lib.make_sam import make_sam_from_state_dict

from lib.demo_helpers.ui.window import DisplayWindow, KEY
from lib.demo_helpers.ui.layout import HStack, VStack
from lib.demo_helpers.ui.buttons import ToggleButton, ImmediateButton
from lib.demo_helpers.ui.sliders import HSlider
from lib.demo_helpers.ui.static import StaticMessageBar

from lib.demo_helpers.shared_ui_layout import PromptUIControl, PromptUI, ReusableBaseImage

from lib.demo_helpers.contours import get_contours_from_mask
from lib.demo_helpers.mask_postprocessing import MaskPostProcessor

from lib.demo_helpers.history_keeper import HistoryKeeper
from lib.demo_helpers.loading import ask_for_path_if_missing, ask_for_model_path_if_missing, load_init_prompts
from lib.demo_helpers.saving import save_segmentation_results
from lib.demo_helpers.misc import (
    get_default_device_string,
    make_device_config,
)


# ---------------------------------------------------------------------------------------------------------------------
# %% Set up script args

# Set argparse defaults
default_device = get_default_device_string()
default_image_path = None
default_model_path = None
default_prompts_path = None
default_display_size = 900
default_base_size = 1024
default_window_size = 16
default_show_iou_preds = False

# Define script arguments
parser = argparse.ArgumentParser(description="Script used to run Segment-Anything (SAM) on a single image")
parser.add_argument("-i", "--image_path", default=default_image_path, help="Path to input image")
parser.add_argument("-m", "--model_path", default=default_model_path, type=str, help="Path to SAM model weights")
parser.add_argument(
    "-p",
    "--prompts_path",
    default=default_prompts_path,
    type=str,
    help="Path to a json file containing initial prompts to use on start-up (see saved json results for formatting)",
)
parser.add_argument(
    "-s",
    "--display_size",
    default=default_display_size,
    type=int,
    help=f"Controls size of displayed results (default: {default_display_size})",
)
parser.add_argument(
    "-d",
    "--device",
    default=default_device,
    type=str,
    help=f"Device to use when running model, such as 'cpu' (default: {default_device})",
)
parser.add_argument(
    "-f32",
    "--use_float32",
    default=False,
    action="store_true",
    help="Use 32-bit floating point model weights. Note: this doubles VRAM usage",
)
parser.add_argument(
    "-ar",
    "--use_aspect_ratio",
    default=False,
    action="store_true",
    help="Process the image at it's original aspect ratio",
)
parser.add_argument(
    "-b",
    "--base_size_px",
    default=default_base_size,
    type=int,
    help="Override base model size (default {default_base_size})",
)
parser.add_argument(
    "-q",
    "--quality_estimate",
    default=default_show_iou_preds,
    action="store_false" if default_show_iou_preds else "store_true",
    help="Hide mask quality estimates" if default_show_iou_preds else "Show mask quality estimates",
)
parser.add_argument(
    "--hide_info",
    default=False,
    action="store_true",
    help="Hide text info elements from UI",
)
parser.add_argument(
    "--enable_promptless_masks",
    default=False,
    action="store_true",
    help="If set, the model will generate mask predictions even when no prompts are given",
)


# For convenience
args = parser.parse_args()
arg_image_path = args.image_path
arg_model_path = args.model_path
int_prompts_path = args.prompts_path
display_size_px = args.display_size
device_str = args.device
use_float32 = args.use_float32
use_square_sizing = not args.use_aspect_ratio
imgenc_base_size = args.base_size_px
show_iou_preds = args.quality_estimate
show_info = not args.hide_info
disable_promptless_masks = not args.enable_promptless_masks

# Set up device config
device_config_dict = make_device_config(device_str, use_float32)

# Create history to re-use selected inputs
history = HistoryKeeper()
_, history_imgpath = history.read("image_path")
_, history_modelpath = history.read("model_path")

# Get pathing to resources, if not provided already
image_path = ask_for_path_if_missing(arg_image_path, "image", history_imgpath)
model_path = ask_for_model_path_if_missing(__file__, arg_model_path, history_modelpath)

# Store history for use on reload
history.store(image_path=image_path, model_path=model_path)


# ---------------------------------------------------------------------------------------------------------------------
# %% Load resources

# Get the model name, for reporting
model_name = osp.basename(model_path)

print("", "Loading model weights...", f"  @ {model_path}", sep="\n", flush=True)
model_config_dict, sammodel = make_sam_from_state_dict(model_path)
sammodel.to(**device_config_dict)

# Load image and get shaping info for providing display
full_image_bgr = cv2.imread(image_path)
if full_image_bgr is None:
    vreader = cv2.VideoCapture(image_path)
    ok_read, full_image_bgr = vreader.read()
    vreader.release()
    if not ok_read:
        print("", "Unable to load image!", f"  @ {image_path}", sep="\n", flush=True)
        raise FileNotFoundError(osp.basename(image_path))


# ---------------------------------------------------------------------------------------------------------------------
# %% Run image encoder

# Run Model
print("", "Encoding image data...", sep="\n", flush=True)
t1 = perf_counter()
encoded_img, token_hw, preencode_img_hw = sammodel.encode_image(full_image_bgr, imgenc_base_size, use_square_sizing)
if torch.cuda.is_available():
    torch.cuda.synchronize()
t2 = perf_counter()
time_taken_ms = round(1000 * (t2 - t1))
print(f"  -> Took {time_taken_ms} ms", flush=True)

# Run model without prompts as sanity check. Also gives initial result values
box_tlbr_norm_list, fg_xy_norm_list, bg_xy_norm_list = [], [], []
encoded_prompts = sammodel.encode_prompts(box_tlbr_norm_list, fg_xy_norm_list, bg_xy_norm_list)
mask_preds, iou_preds = sammodel.generate_masks(
    encoded_img, encoded_prompts, blank_promptless_output=disable_promptless_masks
)
prediction_hw = mask_preds.shape[2:]

# Provide some feedback about how the model is running
model_device = device_config_dict["device"]
model_dtype = str(device_config_dict["dtype"]).split(".")[-1]
image_hw_str = f"{preencode_img_hw[0]} x {preencode_img_hw[1]}"
token_hw_str = f"{token_hw[0]} x {token_hw[1]}"
print(
    "",
    f"Config ({model_name}):",
    f"  Device: {model_device} ({model_dtype})",
    f"  Resolution HW: {image_hw_str}",
    f"  Tokens HW: {token_hw_str}",
    sep="\n",
    flush=True,
)

# Provide memory usage feedback, if using cuda GPU
if model_device == "cuda":
    peak_vram_mb = torch.cuda.max_memory_allocated() // 1_000_000
    print("  VRAM:", peak_vram_mb, "MB")


# ---------------------------------------------------------------------------------------------------------------------
# %% Set up the UI

# Set up shared UI elements & control logic
ui_elems = PromptUI(full_image_bgr, mask_preds)
uictrl = PromptUIControl(ui_elems)

# Set up message bars to communicate data info & controls
device_dtype_str = f"{model_device}/{model_dtype}"
header_msgbar = StaticMessageBar(model_name, f"{token_hw_str} tokens", device_dtype_str, space_equally=True)
footer_msgbar = StaticMessageBar(
    "[p] Preview", "[i] Invert", "[tab] Contouring", "[arrows] Tools/Masks", text_scale=0.35
)

# Set up secondary button controls
show_preview_btn, invert_mask_btn, large_mask_only_btn, pick_best_btn = ToggleButton.many(
    "Preview", "Invert", "Largest Only", "Pick best", default_state=False, text_scale=0.5
)
large_mask_only_btn.toggle(True)
save_btn = ImmediateButton("Save", (60, 170, 20))
secondary_ctrls = HStack(show_preview_btn, invert_mask_btn, large_mask_only_btn, pick_best_btn, save_btn)

# Set up slider controls
thresh_slider = HSlider("Mask Threshold", 0, -8.0, 8.0, 0.1, marker_steps=10)
rounding_slider = HSlider("Round contours", 0, -50, 50, 1, marker_steps=5)
padding_slider = HSlider("Pad contours", 0, -50, 50, 1, marker_steps=5)
simplify_slider = HSlider("Simplify contours", 0, 0, 10, 0.25, marker_steps=4)

# Set up full display layout
disp_layout = VStack(
    header_msgbar if show_info else None,
    ui_elems.layout,
    secondary_ctrls,
    thresh_slider,
    simplify_slider,
    rounding_slider,
    padding_slider,
    footer_msgbar if show_info else None,
).set_debug_name("DisplayLayout")

# Render out an image with a target size, to figure out which side we should limit when rendering
display_image = disp_layout.render(h=display_size_px, w=display_size_px)
render_side = "h" if display_image.shape[1] > display_image.shape[0] else "w"
render_limit_dict = {render_side: display_size_px}
min_display_size_px = disp_layout._rdr.limits.min_h if render_side == "h" else disp_layout._rdr.limits.min_w

# Load initial prompts, if provided
have_init_prompts, init_prompts_dict = load_init_prompts(int_prompts_path)
if have_init_prompts:
    uictrl.load_initial_prompts(init_prompts_dict)


# ---------------------------------------------------------------------------------------------------------------------
# %% Window setup

# Set up display
cv2.destroyAllWindows()
window = DisplayWindow("Display - q to quit", display_fps=60).attach_mouse_callbacks(disp_layout)
window.move(200, 50)

# Change tools/masks on arrow keys
uictrl.attach_arrowkey_callbacks(window)

# Keypress for secondary controls
window.attach_keypress_callback("p", show_preview_btn.toggle)
window.attach_keypress_callback(KEY.TAB, large_mask_only_btn.toggle)
window.attach_keypress_callback("i", invert_mask_btn.toggle)
window.attach_keypress_callback("s", save_btn.click)
window.attach_keypress_callback("c", ui_elems.tools.clear.click)

# For clarity, some additional keypress codes
KEY_ZOOM_IN = ord("=")
KEY_ZOOM_OUT = ord("-")

# Set up helper objects for managing display/mask data
base_img_maker = ReusableBaseImage(full_image_bgr)
mask_postprocessor = MaskPostProcessor()

# Some feedback
print(
    "",
    "Use prompts to segment the image!",
    "- Shift-click to add multiple points",
    "- Right-click to remove points",
    "- Press -/+ keys to change display sizing",
    "- Press q or esc to close the window",
    "",
    sep="\n",
    flush=True,
)

# *** Main display loop ***
try:
    while True:

        # Read prompt input data & selected mask
        need_prompt_encode, box_tlbr_norm_list, fg_xy_norm_list, bg_xy_norm_list = uictrl.read_prompts()
        is_mask_changed, mselect_idx, selected_mask_btn = ui_elems.masks_constraint.read()

        # Read secondary controls
        _, show_mask_preview = show_preview_btn.read()
        is_invert_changed, use_inverted_mask = invert_mask_btn.read()
        _, use_largest_contour = large_mask_only_btn.read()
        _, use_best_mask = pick_best_btn.read()

        # Read sliders
        is_mthresh_changed, mthresh = thresh_slider.read()
        _, msimplify = simplify_slider.read()
        _, mrounding = rounding_slider.read()
        _, mpadding = padding_slider.read()

        # Update post-processor based on control values
        mask_postprocessor.update(use_largest_contour, msimplify, mrounding, mpadding, use_inverted_mask)

        # Only run the model when an input affecting the output has changed!
        if need_prompt_encode:
            encoded_prompts = sammodel.encode_prompts(box_tlbr_norm_list, fg_xy_norm_list, bg_xy_norm_list)
            mask_preds, iou_preds = sammodel.generate_masks(
                encoded_img, encoded_prompts, mask_hint=None, blank_promptless_output=disable_promptless_masks
            )
            if use_best_mask:
                best_mask_idx = sammodel.get_best_mask_index(iou_preds)
                ui_elems.masks_constraint.change_to(best_mask_idx)

        # Update mask previews & selected mask for outlines
        need_mask_update = any((need_prompt_encode, is_mthresh_changed, is_invert_changed, is_mask_changed))
        if need_mask_update:
            selected_mask_uint8 = uictrl.create_hires_mask_uint8(mask_preds, mselect_idx, preencode_img_hw, mthresh)
            uictrl.update_mask_previews(mask_preds, mselect_idx, mthresh, use_inverted_mask)
            if show_iou_preds:
                uictrl.draw_iou_predictions(iou_preds)

        # Process contour data
        final_mask_uint8 = selected_mask_uint8
        ok_contours, mask_contours_norm = get_contours_from_mask(final_mask_uint8, normalize=True)
        if ok_contours:

            # If only 1 fg point prompt is given, use it to hint at selecting largest masks
            point_hint = None
            only_one_fg_pt = len(fg_xy_norm_list) == 1
            no_box_prompt = len(box_tlbr_norm_list) == 0
            if only_one_fg_pt and no_box_prompt:
                point_hint = fg_xy_norm_list[0]
            mask_contours_norm, final_mask_uint8 = mask_postprocessor(final_mask_uint8, mask_contours_norm, point_hint)

        # Re-generate display image at required display size
        # -> Not strictly needed, but can avoid constant re-sizing of base image (helpful for large images)
        display_hw = ui_elems.image.get_render_hw()
        disp_img = base_img_maker.regenerate(display_hw)

        # Update the main display image in the UI
        uictrl.update_main_display_image(disp_img, final_mask_uint8, mask_contours_norm, show_mask_preview)

        # Render final output
        display_image = disp_layout.render(**render_limit_dict)
        req_break, keypress = window.show(display_image)
        if req_break:
            break

        # Scale display size up when pressing +/- keys
        if keypress == KEY_ZOOM_IN:
            display_size_px = min(display_size_px + 50, 10000)
            render_limit_dict = {render_side: display_size_px}
        if keypress == KEY_ZOOM_OUT:
            display_size_px = max(display_size_px - 50, min_display_size_px)
            render_limit_dict = {render_side: display_size_px}

        # Save data
        if save_btn.read():
            disp_image = ui_elems.display_block.rerender()
            all_prompts_dict = {
                "boxes": box_tlbr_norm_list,
                "fg_points": fg_xy_norm_list,
                "bg_points": bg_xy_norm_list,
            }
            save_folder, save_idx = save_segmentation_results(
                image_path, disp_image, mask_contours_norm, selected_mask_uint8, all_prompts_dict
            )
            print(f"SAVED ({save_idx}):", save_folder)

        pass

except KeyboardInterrupt:
    print("", "Closed with Ctrl+C", sep="\n")

except Exception as err:
    raise err

finally:
    cv2.destroyAllWindows()
