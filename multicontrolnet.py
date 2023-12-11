import torch
from diffusers import DiffusionPipeline, AutoencoderKL, ControlNetModel, MotionAdapter
from diffusers.pipelines.controlnet.multicontrolnet import MultiControlNetModel
from PIL import Image
import os
import argparse
import yaml
import datetime
import shutil

def gif2images(gif_filename):
    gif=Image.open(gif_filename)
    frames=[]
    for i in range(gif.n_frames):
        gif.seek(i)
        img = gif.copy()
        frames.append(img)
    return frames

parser = argparse.ArgumentParser()
parser.add_argument(
    '--config',
    type=str,
    required=True,
    help="path to yaml file"
)
args = parser.parse_args()

with open(args.config, "r") as f:
    config_dict = yaml.load(f, Loader=yaml.SafeLoader)

time_str = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
os.makedirs(os.path.join("outputs", time_str), exist_ok=False)
shutil.copyfile(args.config, os.path.join("outputs",time_str, "config.yaml"))

adapter = MotionAdapter.from_pretrained(
    config_dict["motion_module_path"]
)

controlnet_list = config_dict["controlnet"]

controlnet = MultiControlNetModel(
    [ 
        ControlNetModel.from_pretrained(
            x["model_path"],
            torch_dtype=torch.float16
        ) 
        for x in controlnet_list
    ]
)

controlimage = [gif2images(x["image_path"]) for x in controlnet_list]
n_frames = 32 if min([len(x) for x in controlimage])>32 else min([len(x) for x in controlimage])
controlimage = [x[0:n_frames] for x in controlimage]

controlnet_conditioning_scale = [x["conditioning_scale"] for x in controlnet_list]

if config_dict["vae"]["single_file"]:
    vae = AutoencoderKL.from_single_file(
        config_dict["vae"]["model_path"],
        torch_dtype=torch.float16
    )
else:
    vae = AutoencoderKL.from_pretrained(
        config_dict["vae"]["model_path"],
        torch_dtype=torch.float16
    )

model_id = config_dict["pretrained_model_path"]
pipe = DiffusionPipeline.from_pretrained(
    model_id,
    motion_adapter=adapter,
    controlnet=controlnet,
    vae=vae,
    custom_pipeline="custom-pipeline/pipeline_animatediff_controlnet.py",
    torch_dtype=torch.float16
).to("cuda")

if config_dict["lcm_lora"]["enable"]:
    from diffusers import LCMScheduler
    pipe.scheduler = LCMScheduler.from_config(
        pipe.scheduler.config,
        beta_schedule="linear"
    )
    pipe.load_lora_weights(config_dict["lcm_lora"]["model_path"], adapter_name="lcm")
    pipe.set_adapters(["lcm"], adapter_weights=[config_dict["lcm_lora"]["weight"]])
    
else:
    from diffusers import DPMSolverMultistepScheduler
    pipe.scheduler = DPMSolverMultistepScheduler.from_pretrained(
        model_id,
        subfolder="scheduler", 
        beta_schedule="linear",
        clip_sample=False,
        timestep_spacing="linspace",
        steps_offset=1
    )

pipe.enable_vae_slicing()

prompt = config_dict["prompt"]
negative_prompt = config_dict["negative_prompt"]
seed = config_dict["seed"]
steps = config_dict["steps"]
guidance_scale = config_dict["guidance_scale"]

result = pipe(
    prompt=prompt,
    negative_prompt=negative_prompt,
    num_frames=n_frames,
    width=512,
    height=512,
    conditioning_frames=controlimage,
    num_inference_steps=steps,
    guidance_scale=guidance_scale,
    generator=torch.manual_seed(seed),
    controlnet_conditioning_scale=controlnet_conditioning_scale,
).frames[0]

from diffusers.utils import export_to_gif
export_to_gif(result, os.path.join("outputs", time_str, "result.gif"))
