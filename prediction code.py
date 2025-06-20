import torch
import torch.nn as nn
import numpy as np
import os
import json
import random
import matplotlib.pyplot as plt
from torchvision import models

#  Paths 
MODEL_PATH = "/home/Somepalli/mahesh/best_model.pth"
SLICE_DIR = "/home/Somepalli/mahesh/preprocessed_slices"
PRIMARY_METADATA_PATH = "/home/Somepalli/mahesh/slice_metadata.json"
FALLBACK_METADATA_PATH = "/home/Somepalli/mahesh/slice_metadata.json"

# Load model 
class ResNet50Binary(nn.Module):
    def _init_(self):
        super(ResNet50Binary, self)._init_()
        self.model = models.resnet50(pretrained=False)
        self.model.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.model.fc = nn.Linear(self.model.fc.in_features, 2)

    def forward(self, x):
        return self.model(x)

# Set device
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

model = ResNet50Binary()
model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
model.eval()
model.to(DEVICE)

#  Load metadata with exception handling 
try:
    if os.path.exists(PRIMARY_METADATA_PATH):
        with open(PRIMARY_METADATA_PATH, 'r') as f:
            metadata = json.load(f)
        print(f"📂 Loaded metadata from primary path:\n   {PRIMARY_METADATA_PATH}")
    elif os.path.exists(FALLBACK_METADATA_PATH):
        with open(FALLBACK_METADATA_PATH, 'r') as f:
            metadata = json.load(f)
        print(f"📂 Primary not found. Loaded metadata from fallback path:\n   {FALLBACK_METADATA_PATH}")
    else:
        raise FileNotFoundError("❌ Neither metadata file was found.")
except Exception as e:
    print(f"⚠ Error loading metadata: {e}")
    metadata = []
    exit()  # Exit early if metadata is missing

#  Randomly sample 5 slices 
samples = random.sample(metadata, 5)
#  Predict and compare (side-by-side plots) 
fig, axes = plt.subplots(1, 5, figsize=(20, 4))  # 1 row, 5 columns

for i, (fname, true_label) in enumerate(samples):
    # Load slice
    path = os.path.join(SLICE_DIR, fname)
    img = np.load(path)
    img_tensor = torch.tensor(img).unsqueeze(0).unsqueeze(0).float().to(DEVICE)

    # Predict
    with torch.no_grad():
        logits = model(img_tensor)
        pred_label = torch.argmax(logits, dim=1).item()

    # Show in subplot
    axes[i].imshow(img, cmap='gray')
    axes[i].set_title(f"Actual: {true_label}\nPred: {pred_label}")
    axes[i].axis('off')

    print(f"🧾 File: {fname}")
    print(f"✅ Actual Label   : {true_label}")
    print(f"🔮 Predicted Label: {pred_label}")
    print("-" * 40)

plt.tight_layout()
plt.show()
