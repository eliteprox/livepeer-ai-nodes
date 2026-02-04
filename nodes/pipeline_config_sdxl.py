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
                    "max": 0.7,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "ControlNet conditioning strength (observed: 0-0.7, 0=disabled)",
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


IP_ADAPTER_TYPES: Tuple[str, ...] = ("regular",)


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
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "IP Adapter influence scale (observed: 0-1.0)",
                }),
                "type": (IP_ADAPTER_TYPES, {
                    "default": "regular",
                    "tooltip": "IP Adapter type (only 'regular' observed in production)",
                }),
            },
            "optional": {
                "style_image_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "https://eliteencoder.net/assets/img/newyear.png",
                    "tooltip": "URL to style reference image (required when enabled)",
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
        type: str = "regular",
        style_image_url: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        config = {
            "enabled": bool(enabled),
            "scale": float(scale),
            "type": type,
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
                    "tooltip": "SDXL model (only sdxl-turbo is proven with TensorRT)",
                }),
                "width": ("INT", {
                    "default": 512,
                    "min": 256,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Output width (observed: 512, 1024; must be divisible by 8)",
                }),
                "height": ("INT", {
                    "default": 512,
                    "min": 256,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Output height (observed: 512, 1024; must be divisible by 8)",
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
                    "tooltip": "CFG scale (observed: 1.0 in production logs)",
                }),
                "delta": ("FLOAT", {
                    "default": 0.7,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.05,
                    "display": "number",
                    "tooltip": "StreamDiffusion delta (observed: 0.7, 1.0)",
                }),
                "num_inference_steps": ("INT", {
                    "default": 50,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Denoising steps (observed: 50)",
                }),
                "t_index_list": ("STRING", {
                    "default": "5,15,32",
                    "multiline": False,
                    "placeholder": "5,15,32",
                    "tooltip": "Timestep indices (must be non-empty, e.g. [36], [10,20], [5,15,32])",
                }),
                "acceleration": (ACCELERATION_OPTIONS, {
                    "default": "tensorrt",
                    "tooltip": "Hardware acceleration (only 'tensorrt' is proven)",
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
                    "tooltip": "Denoising batch mode (must be true in production)",
                }),
                "do_add_noise": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Add noise during denoising (must be true in production)",
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
                    "tooltip": "Normalize prompt weights (must be true when using weighted prompts)",
                }),
                "normalize_seed_weights": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Normalize seed weights (must be true)",
                }),
                "enable_similar_image_filter": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Filter similar consecutive frames",
                }),
                "similar_image_filter_threshold": ("FLOAT", {
                    "default": 0.98,
                    "min": 0.9,
                    "max": 0.99,
                    "step": 0.01,
                    "display": "number",
                    "tooltip": "Similarity threshold (observed: 0.98-0.99)",
                }),
                "similar_image_filter_max_skip_frame": ("INT", {
                    "default": 10,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Max frames to skip (observed: 1-10)",
                }),
                "use_safety_checker": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable safety checker for NSFW content filtering",
                }),
                "lcm_lora_id": ("STRING", {
                    "default": "latent-consistency/lcm-lora-sdv1-5",
                    "multiline": False,
                    "tooltip": "LCM LoRA model ID (required when use_lcm_lora=true)",
                }),
                "cached_attention_enabled": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Enabled",
                    "label_off": "Disabled",
                    "tooltip": "Enable cached attention for faster inference (rare, advanced)",
                }),
                "cached_attention_interval": ("INT", {
                    "default": 2,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Cached attention interval (frames between cache updates)",
                }),
                "cached_attention_max_frames": ("INT", {
                    "default": 2,
                    "min": 1,
                    "max": 10,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Maximum frames to cache for attention",
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
                "strict_validation": ("BOOLEAN", {
                    "default": False,
                    "label_on": "Strict",
                    "label_off": "Permissive",
                    "tooltip": "Enable strict validation - raises errors for unproven configurations",
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

    @staticmethod
    def _validate_config(
        params: Dict[str, Any],
        ip_adapter: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        Validate configuration against evidence-based rules from production logs.
        
        Returns a list of validation error messages. Empty list means valid.
        
        Rules enforced:
        - Model ID must be 'stabilityai/sdxl-turbo' for TensorRT acceleration
        - If use_lcm_lora=true, lcm_lora_id must be present
        - If IP adapter enabled=true, ip_adapter_style_image_url must be non-empty
        - t_index_list must be non-empty
        - acceleration must be 'tensorrt' (only proven value in logs)
        """
        errors: List[str] = []

        # Rule: t_index_list must be non-empty
        t_index_list = params.get("t_index_list", [])
        if not t_index_list:
            errors.append("t_index_list must be non-empty")

        # Rule: If acceleration is tensorrt, model_id should be sdxl-turbo
        acceleration = params.get("acceleration", "")
        model_id = params.get("model_id", "")
        if acceleration == "tensorrt" and model_id != "stabilityai/sdxl-turbo":
            errors.append(
                f"Model '{model_id}' may not be compatible with TensorRT acceleration. "
                "Only 'stabilityai/sdxl-turbo' is proven in production logs."
            )

        # Rule: If use_lcm_lora=true, lcm_lora_id must be present
        use_lcm_lora = params.get("use_lcm_lora", False)
        lcm_lora_id = params.get("lcm_lora_id", "")
        if use_lcm_lora and not lcm_lora_id:
            errors.append("lcm_lora_id is required when use_lcm_lora=true")

        # Rule: If IP adapter enabled, style_image_url must be present
        if ip_adapter is not None and isinstance(ip_adapter, dict):
            ip_config = ip_adapter.get("ip_adapter", {})
            if ip_config.get("enabled", False):
                style_url = ip_adapter.get("ip_adapter_style_image_url", "")
                if not style_url:
                    errors.append(
                        "ip_adapter_style_image_url is required when IP adapter is enabled"
                    )

        # Rule: acceleration should be tensorrt (only proven value)
        if acceleration and acceleration != "tensorrt":
            errors.append(
                f"acceleration='{acceleration}' is not proven in production logs. "
                "Only 'tensorrt' is validated."
            )

        return errors

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
        use_safety_checker: bool = True,
        lcm_lora_id: str = "latent-consistency/lcm-lora-sdv1-5",
        cached_attention_enabled: bool = False,
        cached_attention_interval: int = 2,
        cached_attention_max_frames: int = 2,
        controlnet_1: Optional[Dict[str, Any]] = None,
        controlnet_2: Optional[Dict[str, Any]] = None,
        controlnet_3: Optional[Dict[str, Any]] = None,
        ip_adapter: Optional[Dict[str, Any]] = None,
        strict_validation: bool = False,
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
            "use_safety_checker": bool(use_safety_checker),
        }

        # Add LCM LoRA ID if LCM LoRA is enabled
        if use_lcm_lora and lcm_lora_id:
            params["lcm_lora_id"] = lcm_lora_id.strip()

        # Add cached attention config if enabled
        if cached_attention_enabled:
            params["cached_attention"] = {
                "enabled": True,
                "interval": int(cached_attention_interval),
                "max_frames": int(cached_attention_max_frames),
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
                ip_config = ip_adapter["ip_adapter"]
                # Ensure type field is included (default to "regular" if missing)
                if "type" not in ip_config:
                    ip_config["type"] = "regular"
                params["ip_adapter"] = ip_config
            if "ip_adapter_style_image_url" in ip_adapter:
                url = ip_adapter["ip_adapter_style_image_url"]
                if url:
                    params["ip_adapter_style_image_url"] = url

        # Validate configuration
        validation_errors = self._validate_config(params, ip_adapter)
        if validation_errors:
            if strict_validation:
                raise ValueError(
                    "Configuration validation failed:\n- " +
                    "\n- ".join(validation_errors)
                )
            else:
                for error in validation_errors:
                    LOGGER.warning("Config validation: %s", error)

        # Return model_id as "streamdiffusion-sdxl" for the pipeline identifier
        pipeline_model_id = "streamdiffusion-sdxl"

        # Single pipeline_params dict output is enough; TrickleConfig accepts it optionally.
        return (params, pipeline_model_id)


# ---------------------------------------------------------------------------
# Preset Configuration Node
# ---------------------------------------------------------------------------

# Preset names tuple for dropdown
PRESET_OPTIONS: Tuple[str, ...] = ("minimal", "standard", "high_quality")


class StreamDiffusionSDXLPreset:
    """
    Preset configuration node for StreamDiffusion SDXL pipeline.
    
    Provides canonical, validated configurations based on production logs.
    Use these as safe starting points for AI agents to modify.
    
    Presets:
    - minimal: Bare minimum required parameters (from guide section 13)
    - standard: Production-ready defaults with proven values
    - high_quality: Higher quality settings with more inference steps
    """

    # Canonical minimal valid config from guide section 13
    PRESET_MINIMAL: Dict[str, Any] = {
        "model_id": "stabilityai/sdxl-turbo",
        "prompt": "example",
        "width": 512,
        "height": 512,
        "num_inference_steps": 50,
        "guidance_scale": 1,
        "seed": 42,
        "delta": 0.7,
        "t_index_list": [1],
        "seed_interpolation_method": "linear",
        "normalize_seed_weights": True,
        "prompt_interpolation_method": "linear",
        "normalize_prompt_weights": True,
        "acceleration": "tensorrt",
        "use_denoising_batch": True,
        "do_add_noise": True,
    }

    # Standard production-ready config
    PRESET_STANDARD: Dict[str, Any] = {
        "model_id": "stabilityai/sdxl-turbo",
        "prompt": "blooming flower",
        "negative_prompt": "blurry, low quality, flat, 2d",
        "width": 512,
        "height": 512,
        "num_inference_steps": 50,
        "guidance_scale": 1,
        "seed": 789,
        "delta": 0.7,
        "t_index_list": [5, 15, 32],
        "seed_interpolation_method": "linear",
        "normalize_seed_weights": True,
        "prompt_interpolation_method": "slerp",
        "normalize_prompt_weights": True,
        "acceleration": "tensorrt",
        "use_denoising_batch": True,
        "do_add_noise": True,
        "use_lcm_lora": True,
        "lcm_lora_id": "latent-consistency/lcm-lora-sdv1-5",
        "use_safety_checker": True,
        "enable_similar_image_filter": False,
        "similar_image_filter_threshold": 0.98,
        "similar_image_filter_max_skip_frame": 10,
    }

    # High quality config with more steps
    PRESET_HIGH_QUALITY: Dict[str, Any] = {
        "model_id": "stabilityai/sdxl-turbo",
        "prompt": "blooming flower",
        "negative_prompt": "blurry, low quality, flat, 2d, deformed, bad anatomy",
        "width": 1024,
        "height": 1024,
        "num_inference_steps": 50,
        "guidance_scale": 1,
        "seed": 789,
        "delta": 0.7,
        "t_index_list": [10, 20, 30, 40],
        "seed_interpolation_method": "linear",
        "normalize_seed_weights": True,
        "prompt_interpolation_method": "slerp",
        "normalize_prompt_weights": True,
        "acceleration": "tensorrt",
        "use_denoising_batch": True,
        "do_add_noise": True,
        "use_lcm_lora": True,
        "lcm_lora_id": "latent-consistency/lcm-lora-sdv1-5",
        "use_safety_checker": True,
        "enable_similar_image_filter": True,
        "similar_image_filter_threshold": 0.99,
        "similar_image_filter_max_skip_frame": 5,
    }

    PRESETS: Dict[str, Dict[str, Any]] = {
        "minimal": PRESET_MINIMAL,
        "standard": PRESET_STANDARD,
        "high_quality": PRESET_HIGH_QUALITY,
    }

    @classmethod
    def INPUT_TYPES(cls) -> Dict[str, Any]:
        return {
            "required": {
                "preset": (PRESET_OPTIONS, {
                    "default": "standard",
                    "tooltip": "Select a preset configuration",
                }),
            },
            "optional": {
                "prompt_override": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Override prompt (leave empty to use preset default)",
                    "tooltip": "Override the prompt from the preset",
                }),
                "seed_override": ("INT", {
                    "default": -1,
                    "min": -1,
                    "max": 2**32 - 1,
                    "step": 1,
                    "display": "number",
                    "tooltip": "Override seed (-1 to use preset default)",
                }),
                "width_override": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Override width (0 to use preset default)",
                }),
                "height_override": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 1024,
                    "step": 8,
                    "display": "number",
                    "tooltip": "Override height (0 to use preset default)",
                }),
            },
        }

    RETURN_TYPES = ("DICT", "STRING")
    RETURN_NAMES = ("pipeline_params", "config_json")
    FUNCTION = "create_preset_config"
    CATEGORY = "Livepeer/StreamDiffusion-SDXL"

    def create_preset_config(
        self,
        preset: str,
        prompt_override: str = "",
        seed_override: int = -1,
        width_override: int = 0,
        height_override: int = 0,
    ) -> Tuple[Dict[str, Any], str]:
        # Get the base preset config (make a copy to avoid mutating the class constant)
        if preset not in self.PRESETS:
            LOGGER.warning("Unknown preset '%s', using 'standard'", preset)
            preset = "standard"
        
        config = dict(self.PRESETS[preset])

        # Apply overrides if provided
        if prompt_override and prompt_override.strip():
            config["prompt"] = prompt_override.strip()
        
        if seed_override >= 0:
            config["seed"] = int(seed_override)
        
        if width_override > 0:
            # Ensure divisible by 8
            config["width"] = max(256, (width_override // 8) * 8)
        
        if height_override > 0:
            # Ensure divisible by 8
            config["height"] = max(256, (height_override // 8) * 8)

        config_json = json.dumps(config, indent=2)
        return (config, config_json)


# ---------------------------------------------------------------------------
# Node Registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "StreamDiffusionSDXLConfig": StreamDiffusionSDXLConfig,
    "StreamDiffusionSDXLControlNet": StreamDiffusionSDXLControlNet,
    "StreamDiffusionSDXLIPAdapter": StreamDiffusionSDXLIPAdapter,
    "StreamDiffusionSDXLPreset": StreamDiffusionSDXLPreset,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StreamDiffusionSDXLConfig": "StreamDiffusion SDXL Config",
    "StreamDiffusionSDXLControlNet": "StreamDiffusion SDXL ControlNet",
    "StreamDiffusionSDXLIPAdapter": "StreamDiffusion SDXL IP Adapter",
    "StreamDiffusionSDXLPreset": "StreamDiffusion SDXL Preset",
}
