import os
import nibabel as nib
import numpy as np
import json
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import models
from sklearn.model_selection import train_test_split
from torch.cuda.amp import autocast, GradScaler
from tqdm import tqdm
import time

# CONFIG

CT_DIR = "/home/Somepalli/mahesh/CECT_data/ct_files/"
MASK_DIR = "/home/Somepalli/mahesh/CECT_data/mask_files/"
OUTPUT_DIR = "preprocessed_slices/"
METADATA_PATH = "slice_metadata.json"
USE_PERCENTAGE = 0.40  # Use only 10% of data
BATCH_SIZE = 4
NUM_EPOCHS = 10
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# STEP 1: Preprocess .nii.gz to .npy

def preprocess_nifti(ct_dir, mask_dir, output_dir, metadata_path):
    os.makedirs(output_dir, exist_ok=True)
    ct_files = sorted([f for f in os.listdir(ct_dir) if f.endswith(".nii.gz")])
    mask_files = sorted([f for f in os.listdir(mask_dir) if f.endswith(".nii.gz")])

    matched_ct_files = []
    matched_mask_files = []

    for ct_file in ct_files:
        base = ct_file.split("_ct")[0]
        match = [m for m in mask_files if base in m]
        if match:
            matched_ct_files.append(ct_file)
            matched_mask_files.append(match[0])

    print(f"✅ Matched CT-Mask pairs: {len(matched_ct_files)}")

    slice_metadata = []

    for ct_file, mask_file in tqdm(zip(matched_ct_files, matched_mask_files), total=len(matched_ct_files), desc="Preprocessing"):
        ct_path = os.path.join(ct_dir, ct_file)
        mask_path = os.path.join(mask_dir, mask_file)

        try:
            ct_img = nib.load(ct_path).get_fdata()
            mask_img = nib.load(mask_path).get_fdata()

            if ct_img.shape != mask_img.shape:
                continue

            for i in range(ct_img.shape[2]):
                label = 1 if np.max(mask_img[:, :, i]) > 0 else 0
                ct_slice = ct_img[:, :, i]
                ct_slice = (ct_slice - np.min(ct_slice)) / (np.max(ct_slice) - np.min(ct_slice) + 1e-8)

                filename = f"{ct_file.replace('.nii.gz', '')}slice{i}label{label}.npy"
                np.save(os.path.join(output_dir, filename), ct_slice)
                slice_metadata.append((filename, label))
        except Exception as e:
            print(f"❌ Error with {ct_file}: {e}")
            continue

    with open(metadata_path, "w") as f:
        json.dump(slice_metadata, f)

    print(f"✅ Preprocessing complete. {len(slice_metadata)} slices saved.")
    return slice_metadata


# STEP 2: Dataset Class

class NPYSliceDataset(Dataset):
    def _init_(self, slice_metadata, root):
        self.slice_metadata = slice_metadata
        self.root = root

    def _len_(self):
        return len(self.slice_metadata)

    def _getitem_(self, idx):
        fname, label = self.slice_metadata[idx]
        ct_slice = np.load(os.path.join(self.root, fname))
        ct_slice = np.expand_dims(ct_slice, axis=0).astype(np.float32)
        return torch.tensor(ct_slice), label


# STEP 3: Model Definition

class ResNet50Binary(nn.Module):
    def _init_(self):
        super(ResNet50Binary, self)._init_()
        self.model = models.resnet50(pretrained=True)
        self.model.conv1 = nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.model.fc = nn.Linear(self.model.fc.in_features, 2)

    def forward(self, x):
        return self.model(x)


# STEP 4: Training & Validation

def train_one_epoch(model, loader, optimizer, criterion, scaler):
    model.train()
    total_loss, correct = 0.0, 0

    for inputs, labels in tqdm(loader, desc="Training", leave=False):
        inputs, labels = inputs.to(DEVICE), labels.to(DEVICE)
        optimizer.zero_grad()

        with autocast():
            outputs = model(inputs)
            loss = criterion(outputs, labels)

        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

        total_loss += loss.item() * inputs.size(0)
        correct += (outputs.argmax(1) == labels).sum().item()

    return total_loss / len(loader.dataset), correct / len(loader.dataset)

def validate(model, loader, criterion):
    model.eval()
    total_loss, correct = 0.0, 0

    with torch.no_grad():
        for inputs, labels in tqdm(loader, desc="Validating", leave=False):
            inputs, labels = inputs.to(DEVICE), labels.to(DEVICE)
            with autocast():
                outputs = model(inputs)
                loss = criterion(outputs, labels)

            total_loss += loss.item() * inputs.size(0)
            correct += (outputs.argmax(1) == labels).sum().item()

    return total_loss / len(loader.dataset), correct / len(loader.dataset)


# MAIN FUNCTION

def main():
    # Step 1: Preprocess if needed
    if not os.path.exists(METADATA_PATH):
        slice_metadata = preprocess_nifti(CT_DIR, MASK_DIR, OUTPUT_DIR, METADATA_PATH)
    else:
        with open(METADATA_PATH, "r") as f:
            slice_metadata = json.load(f)

    # Step 2: Use only a portion of the dataset
    slice_metadata = slice_metadata[:int(len(slice_metadata) * USE_PERCENTAGE)]
    print(f"📊 Using {len(slice_metadata)} slices")

    # Step 3: Train/Val split
    train_meta, val_meta = train_test_split(slice_metadata, test_size=0.1, stratify=[x[1] for x in slice_metadata])
    train_dataset = NPYSliceDataset(train_meta, OUTPUT_DIR)
    val_dataset = NPYSliceDataset(val_meta, OUTPUT_DIR)

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=0, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=0, pin_memory=True)

    # Step 4: Model, Loss, Optimizer
    model = ResNet50Binary().to(DEVICE)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-4)
    scaler = GradScaler()

    best_acc = 0
    for epoch in range(NUM_EPOCHS):
        print(f"\n📘 Epoch {epoch+1}/{NUM_EPOCHS}")
        start = time.time()

        train_loss, train_acc = train_one_epoch(model, train_loader, optimizer, criterion, scaler)
        val_loss, val_acc = validate(model, val_loader, criterion)

        print(f"✅ Train Loss: {train_loss:.4f} | Acc: {train_acc:.4f}")
        print(f"🔍 Val   Loss: {val_loss:.4f} | Acc: {val_acc:.4f}")
        print(f"⏱ Time: {time.time() - start:.2f}s")

        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), "best_model.pth")
            print("💾 Saved best model!")

    print("\n🏁 Training complete.")

if _name_ == "_main_":
    main()
