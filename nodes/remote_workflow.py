"""Build editable remote API workflows without loading a local model."""
import json


class FluxKleinWorkflow:
    CATEGORY = "Livepeer/Remote Session"
    FUNCTION = "build"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("workflow_json",)
    DESCRIPTION = "Connect to Open Remote Session or Update Remote Workflow. Requires FluxKleinStream on the remote runner. JSON override replaces the entire generated graph."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "prompt": ("STRING", {"default": "A luminous botanical landscape", "multiline": True}),
            "seed": ("INT", {"default": 42, "min": -1, "max": 2147483647}),
            "input_blend": ("FLOAT", {"default": .5, "min": 0., "max": 1.}),
            "steps": ("INT", {"default": 4, "min": 1, "max": 8}),
            "guidance": ("FLOAT", {"default": 1., "min": 0., "max": 8.}),
            "feedback": ("FLOAT", {"default": .5, "min": 0., "max": 1.}),
            "width": ("INT", {"default": 384, "min": 64, "max": 1024, "step": 16}),
            "height": ("INT", {"default": 384, "min": 64, "max": 1024, "step": 16}),
        }, "optional": {"workflow_override": ("STRING", {"default": "", "multiline": True})}}

    def build(self, prompt, seed, input_blend, steps, guidance, feedback, width, height, workflow_override=""):
        if workflow_override.strip():
            graph = json.loads(workflow_override)
            if not isinstance(graph, dict) or not graph or not all(
                isinstance(node, dict) and isinstance(node.get("class_type"), str)
                and isinstance(node.get("inputs"), dict) for node in graph.values()
            ):
                raise ValueError("Override must be a ComfyUI API-format workflow with class_type and inputs per node")
        else:
            graph = {
                "1": {"class_type": "LoadTensor", "inputs": {}},
                "2": {"class_type": "FluxKleinStream", "inputs": dict(
                    image=["1", 0], prompt=prompt, seed=seed, input_blend=input_blend,
                    steps=steps, guidance=guidance, feedback=feedback, width=width, height=height)},
                "3": {"class_type": "SaveTensor", "inputs": {"images": ["2", 0]}},
            }
        return (json.dumps(graph, indent=2),)


NODE_CLASS_MAPPINGS = {"FluxKleinWorkflow": FluxKleinWorkflow}
NODE_DISPLAY_NAME_MAPPINGS = {"FluxKleinWorkflow": "Livepeer · Flux Klein Workflow"}
