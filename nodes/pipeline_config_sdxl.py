"""
StreamDiffusion SDXL Pipeline Configuration Nodes for ComfyUI.

Generates validated pipeline payloads matching the Livepeer AI Gateway
streamdiffusion-sdxl pipeline schema. These are optional addon nodes
that produce JSON outputs for configuring real-time AI video processing.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("comfyui_trickle.pipeline_config_sdxl")

# ---------------------------------------------------------------------------
# Constants and Enums
# ---------------------------------------------------------------------------

ACCELERATION_OPTIONS: Tuple[str, ...] = ("none", "xformers", "sfast", "tensorrt")
INTERPOLATION_METHODS: Tuple[str, ...] = ("linear", "slerp")

# Valid model options for streamdiffusion-sdxl pipeline
SDXL_MODEL_OPTIONS: Tuple[str, ...] = (
    "stabilityai/sdxl-turbo",
    "stabilityai/sd-turbo",
    "prompthero/openjourney-v4",
    "Lykon/dreamshaper-8",
)

# ControlNet model options for SDXL
CONTROLNET_MODEL_OPTIONS: Tuple[str, ...] = (
    "xinsir/controlnet-depth-sdxl-1.0",
    "xinsir/controlnet-canny-sdxl-1.0",
    "xinsir/controlnet-tile-sdxl-1.0",
)

# ControlNet preprocessor options
CONTROLNET_PREPROCESSOR_OPTIONS: Tuple[str, ...] = (
    "depth_tensorrt",
    "canny",
    "feedback",
    "passthrough",
)


# ---------------------------------------------------------------------------
# ControlNet Configuration Node
# ---------------------------------------------------------------------------


class StreamDiffusionSDXLControlNet:
    """
    ControlNet configuration node for StreamDiffusion SDXL pipeline.
    
    Outputs a CONTROLNET_CONFIG_SDXL dict that can be connected to the
    main StreamDiffusionSDXLConfig node.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable/disable this ControlNet",
                }),
                "model_id": (CONTROLNET_MODEL_OPTIONS, {
                    "default": CONTROLNET_MODEL_OPTIONS[0],
                    "tooltip": "ControlNet model identifier",
                }),
                "preprocessor": (CONTROLNET_PREPROCESSOR_OPTIONS, {
                    "default": CONTROLNET_PREPROCESSOR_OPTIONS[0],
                    "tooltip": "Preprocessor to apply to input frames",
                }),
                "conditioning_scale": ("FLOAT", {
                    "default": 0.4,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "ControlNet conditioning strength (0 = disabled)",
                }),
            },
            "optional": {
                "control_guidance_start": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "When to start applying ControlNet guidance (0-1)",
                }),
                "control_guidance_end": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "When to stop applying ControlNet guidance (0-1)",
                }),
                "preprocessor_params_json": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                    "placeholder": '{"low_threshold": 100, "high_threshold": 200}',
                    "tooltip": "Additional preprocessor parameters as JSON",
                }),
            },
        }

    RETURN_TYPES = ("CONTROLNET_CONFIG_SDXL", "STRING")
    RETURN_NAMES = ("controlnet_config", "config_json")
    FUNCTION = "create_controlnet_config"
    CATEGORY = "Livepeer/StreamDiffusion-SDXL"

    def create_controlnet_config(
        self,
        enabled: bool,
        model_id: str,
        preprocessor: str,
        conditioning_scale: float,
        control_guidance_start: float = 0.0,
        control_guidance_end: float = 1.0,
        preprocessor_params_json: str = "{}",
    ) -> Tuple[Dict[str, Any], str]:
        # Parse preprocessor params
        preprocessor_params = {}
        if preprocessor_params_json and preprocessor_params_json.strip():
            try:
                preprocessor_params = json.loads(preprocessor_params_json.strip())
                if not isinstance(preprocessor_params, dict):
                    preprocessor_params = {}
            except json.JSONDecodeError:
                LOGGER.warning("Invalid preprocessor_params JSON, using empty dict")
                preprocessor_params = {}

        config = {
            "enabled": bool(enabled),
            "model_id": model_id.strip(),
            "preprocessor": preprocessor.strip(),
            "conditioning_scale": float(conditioning_scale),
            "control_guidance_start": float(control_guidance_start),
            "control_guidance_end": float(control_guidance_end),
            "preprocessor_params": preprocessor_params,
        }

        config_json = json.dumps(config, indent=2)
        return (config, config_json)


# ---------------------------------------------------------------------------
# IP Adapter Configuration Node
# ---------------------------------------------------------------------------


class StreamDiffusionSDXLIPAdapter:
    """
    IP Adapter configuration node for StreamDiffusion SDXL pipeline.
    
    Outputs an IP_ADAPTER_CONFIG_SDXL dict that can be connected to the
    main StreamDiffusionSDXLConfig node.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "enabled": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable/disable IP Adapter",
                }),
                "scale": ("FLOAT", {
                    "default": 0.5,
                    "min": 0.0,
                    "max": 2.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "IP Adapter influence scale",
                }),
            },
            "optional": {
                "style_image_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "https://eliteencoder.net/assets/img/newyear.png",
                    "tooltip": "URL to style reference image for IP Adapter",
                }),
            },
        }

    RETURN_TYPES = ("IP_ADAPTER_CONFIG_SDXL", "STRING")
    RETURN_NAMES = ("ip_adapter_config", "config_json")
    FUNCTION = "create_ip_adapter_config"
    CATEGORY = "Livepeer/StreamDiffusion-SDXL"

    def create_ip_adapter_config(
        self,
        enabled: bool,
        scale: float,
        style_image_url: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        config = {
            "enabled": bool(enabled),
            "scale": float(scale),
        }

        style_url = style_image_url.strip() if style_image_url else ""

        result = {
            "ip_adapter": config,
            "ip_adapter_style_image_url": style_url,
        }

        config_json = json.dumps(result, indent=2)
        return (result, config_json)


# ---------------------------------------------------------------------------
# Main StreamDiffusion SDXL Configuration Node
# ---------------------------------------------------------------------------


class StreamDiffusionSDXLConfig:
    """
    Main configuration node for StreamDiffusion SDXL pipeline.
    
    Generates a complete pipeline configuration payload that matches the
    Livepeer AI Gateway streamdiffusion-sdxl schema. Outputs both a Python
    dict and a JSON string preview.
    
    Connect optional ControlNet and IP Adapter nodes for additional features.
    """

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "blooming flower",
                    "multiline": True,
                    "placeholder": "Enter your prompt here",
                    "tooltip": "Text prompt for image generation",
                }),
                "model_id": (SDXL_MODEL_OPTIONS, {
                    "default": SDXL_MODEL_OPTIONS[0],
                    "tooltip": "SDXL model to use for inference",
                }),
                "width": ("INT", {
                    "default": 512,
                    "min": 256,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Output width (must be divisible by 8)",
                }),
                "height": ("INT", {
                    "default": 512,
                    "min": 256,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Output height (must be divisible by 8)",
                }),
                "seed": ("INT", {
                    "default": 789,
                    "min": 0,
                    "max": 2**32 - 1,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Random seed for reproducibility",
                }),
            },
            "optional": {
                "negative_prompt": ("STRING", {
                    "default": "blurry, low quality, flat, 2d",
                    "multiline": True,
                    "placeholder": "Negative prompt (optional)",
                    "tooltip": "Negative prompt to guide away from undesired features",
                }),
                "guidance_scale": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 20.0,
                    "step": 0.1,
                    "display": "number",
                    "tooltip": "How strongly to follow the prompt (CFG scale)",
                }),
                "delta": ("FLOAT", {
                    "default": 0.7,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "StreamDiffusion delta parameter for temporal consistency",
                }),
                "num_inference_steps": ("INT", {
                    "default": 50,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Number of denoising steps",
                }),
                "t_index_list": ("STRING", {
                    "default": "5,15,32",
                    "multiline": False,
                    "placeholder": "5,15,32",
                    "tooltip": "Comma-separated timestep indices for StreamDiffusion",
                }),
                "acceleration": (ACCELERATION_OPTIONS, {
                    "default": "tensorrt",
                    "tooltip": "Hardware acceleration method",
                }),
                "use_lcm_lora": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Use LCM LoRA for faster inference",
                }),
                "use_denoising_batch": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable denoising batch mode for StreamDiffusion",
                }),
                "do_add_noise": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Add noise during denoising process",
                }),
                "prompt_interpolation_method": (INTERPOLATION_METHODS, {
                    "default": "slerp",
                    "tooltip": "Method for interpolating between prompts",
                }),
                "seed_interpolation_method": (INTERPOLATION_METHODS, {
                    "default": "linear",
                    "tooltip": "Method for interpolating between seeds",
                }),
                "normalize_prompt_weights": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Normalize prompt weights",
                }),
                "normalize_seed_weights": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Normalize seed weights",
                }),
                "enable_similar_image_filter": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Filter similar consecutive frames",
                }),
                "similar_image_filter_threshold": ("FLOAT", {
                    "default": 0.98,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "display": "number",
                    "tooltip": "Similarity threshold for frame filtering",
                }),
                "similar_image_filter_max_skip_frame": ("INT", {
                    "default": 10,
                    "min": 0,
                    "max": 100,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Maximum frames to skip when filtering similar images",
                }),
                "controlnet_1": ("CONTROLNET_CONFIG_SDXL", {
                    "tooltip": "Connect StreamDiffusion SDXL ControlNet node (optional)",
                }),
                "controlnet_2": ("CONTROLNET_CONFIG_SDXL", {
                    "tooltip": "Connect StreamDiffusion SDXL ControlNet node (optional)",
                }),
                "controlnet_3": ("CONTROLNET_CONFIG_SDXL", {
                    "tooltip": "Connect StreamDiffusion SDXL ControlNet node (optional)",
                }),
                "ip_adapter": ("IP_ADAPTER_CONFIG_SDXL", {
                    "tooltip": "Connect StreamDiffusion SDXL IP Adapter node (optional)",
                }),
            },
        }

    RETURN_TYPES = ("DICT", "STRING")
    RETURN_NAMES = ("pipeline_params", "model_id_out")
    FUNCTION = "create_pipeline_config"
    CATEGORY = "Livepeer/StreamDiffusion-SDXL"

    @staticmethod
    def _parse_t_index_list(value: str) -> List[int]:
        """Parse comma-separated t-index list into integers."""
        if not value or not value.strip():
            return [5, 15, 32]  # Default from log

        indices = []
        for token in value.split(","):
            token = token.strip()
            if token:
                try:
                    indices.append(int(token))
                except ValueError:
                    LOGGER.warning("Invalid t_index value '%s', skipping", token)

        return indices if indices else [5, 15, 32]

    def create_pipeline_config(
        self,
        prompt: str,
        model_id: str,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str = "blurry, low quality, flat, 2d",
        guidance_scale: float = 1.0,
        delta: float = 0.7,
        num_inference_steps: int = 50,
        t_index_list: str = "5,15,32",
        acceleration: str = "tensorrt",
        use_lcm_lora: bool = True,
        use_denoising_batch: bool = True,
        do_add_noise: bool = True,
        prompt_interpolation_method: str = "slerp",
        seed_interpolation_method: str = "linear",
        normalize_prompt_weights: bool = True,
        normalize_seed_weights: bool = True,
        enable_similar_image_filter: bool = False,
        similar_image_filter_threshold: float = 0.98,
        similar_image_filter_max_skip_frame: int = 10,
        controlnet_1: Optional[Dict[str, Any]] = None,
        controlnet_2: Optional[Dict[str, Any]] = None,
        controlnet_3: Optional[Dict[str, Any]] = None,
        ip_adapter: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        # Validate dimensions
        width = max(256, (width // 8) * 8)
        height = max(256, (height // 8) * 8)

        # Parse t_index_list
        t_indices = self._parse_t_index_list(t_index_list)

        # Build core params
        params: Dict[str, Any] = {
            "acceleration": acceleration,
            "model_id": model_id.strip(),
            "prompt": prompt.strip(),
            "negative_prompt": negative_prompt.strip() if negative_prompt else "",
            "guidance_scale": float(guidance_scale),
            "delta": float(delta),
            "num_inference_steps": int(num_inference_steps),
            "width": int(width),
            "height": int(height),
            "seed": int(seed),
            "t_index_list": t_indices,
            "use_lcm_lora": bool(use_lcm_lora),
            "use_denoising_batch": bool(use_denoising_batch),
            "do_add_noise": bool(do_add_noise),
            "prompt_interpolation_method": prompt_interpolation_method,
            "seed_interpolation_method": seed_interpolation_method,
            "normalize_prompt_weights": bool(normalize_prompt_weights),
            "normalize_seed_weights": bool(normalize_seed_weights),
            "enable_similar_image_filter": bool(enable_similar_image_filter),
            "similar_image_filter_threshold": float(similar_image_filter_threshold),
            "similar_image_filter_max_skip_frame": int(similar_image_filter_max_skip_frame),
        }

        # Collect controlnets
        controlnets = []
        for cn in [controlnet_1, controlnet_2, controlnet_3]:
            if cn is not None and isinstance(cn, dict):
                controlnets.append(cn)

        if controlnets:
            params["controlnets"] = controlnets

        # Add IP adapter if provided
        if ip_adapter is not None and isinstance(ip_adapter, dict):
            if "ip_adapter" in ip_adapter:
                params["ip_adapter"] = ip_adapter["ip_adapter"]
            if "ip_adapter_style_image_url" in ip_adapter:
                url = ip_adapter["ip_adapter_style_image_url"]
                if url:
                    params["ip_adapter_style_image_url"] = url

        # Return model_id as "streamdiffusion-sdxl" for the pipeline identifier
        pipeline_model_id = "streamdiffusion-sdxl"

        # Single pipeline_params dict output is enough; TrickleConfig accepts it optionally.
        return (params, pipeline_model_id)


# ---------------------------------------------------------------------------
# Node Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "StreamDiffusionSDXLConfig": StreamDiffusionSDXLConfig,
    "StreamDiffusionSDXLControlNet": StreamDiffusionSDXLControlNet,
    "StreamDiffusionSDXLIPAdapter": StreamDiffusionSDXLIPAdapter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StreamDiffusionSDXLConfig": "StreamDiffusion SDXL Config",
    "StreamDiffusionSDXLControlNet": "StreamDiffusion SDXL ControlNet",
    "StreamDiffusionSDXLIPAdapter": "StreamDiffusion SDXL IP Adapter",
}
