#include "UEFogReadback.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "RenderGraphUtils.h"
#include "Serialization/JsonSerializer.h"

FUEFogReadback::FUEFogReadback()
{
    FParse::Value(FCommandLine::Get(), TEXT("FogLabReadback="), Directory);
    FParse::Value(FCommandLine::Get(), TEXT("FogLabReadbackFrame="), TargetFrame);
    TargetFrame = FMath::Max(1, TargetFrame);
}

bool FUEFogReadback::WantsFrame()
{
    if (Directory.IsEmpty() || bScheduled || bFinished) return false;
    if (++SeenFrames < TargetFrame) return false;
    bScheduled = true;
    Metadata = MakeShared<FJsonObject>();
    Metadata->SetNumberField(TEXT("schemaVersion"), 1);
    Metadata->SetNumberField(TEXT("captureViewIndex"), SeenFrames);
    return true;
}

void FUEFogReadback::Add(FRDGBuilder& GraphBuilder, FRDGTextureRef Texture, const FString& Name)
{
    FItem Item;
    Item.Name = Name;
    Item.Size = Texture->Desc.Extent;
    Item.Format = Texture->Desc.Format;
    Item.Readback = MakeUnique<FRHIGPUTextureReadback>(FName(*Name));
    AddEnqueueCopyPass(GraphBuilder, Item.Readback.Get(), Texture);
    Items.Add(MoveTemp(Item));
}

void FUEFogReadback::Fail(const FString& Reason)
{
    bFinished = true;
    IFileManager::Get().MakeDirectory(*Directory, true);
    FFileHelper::SaveStringToFile(Reason, *FPaths::Combine(Directory, TEXT("failed.txt")));
    UE_LOG(LogTemp, Error, TEXT("UEFog readback failed: %s"), *Reason);
}

void FUEFogReadback::Poll()
{
    if (!bScheduled || bFinished || Items.IsEmpty()) return;
    for (const FItem& Item : Items) if (!Item.Readback->IsReady()) return;
    if (!IFileManager::Get().MakeDirectory(*Directory, true)) { Fail(TEXT("Cannot create output directory")); return; }
    TArray<TSharedPtr<FJsonValue>> Files;
    for (FItem& Item : Items)
    {
        const int32 PixelBytes = GPixelFormats[Item.Format].BlockBytes;
        int32 Pitch = 0, BufferHeight = 0;
        const uint8* Data = static_cast<const uint8*>(Item.Readback->Lock(Pitch, &BufferHeight));
        if (!Data || Pitch < Item.Size.X || BufferHeight < Item.Size.Y)
        {
            if (Data) Item.Readback->Unlock();
            Fail(TEXT("Empty readback or invalid staging pitch/height")); return;
        }
        TArray<uint8> Packed;
        Packed.SetNumUninitialized(Item.Size.X * Item.Size.Y * PixelBytes);
        for (int32 Y = 0; Y < Item.Size.Y; ++Y)
            FMemory::Memcpy(Packed.GetData() + Y * Item.Size.X * PixelBytes, Data + Y * Pitch * PixelBytes, Item.Size.X * PixelBytes);
        Item.Readback->Unlock();
        const FString Filename = Item.Name + TEXT(".bin");
        if (!FFileHelper::SaveArrayToFile(Packed, *FPaths::Combine(Directory, Filename)))
        { Fail(TEXT("Cannot write packed texture")); return; }
        auto Record = MakeShared<FJsonObject>();
        Record->SetStringField(TEXT("file"), Filename);
        Record->SetNumberField(TEXT("width"), Item.Size.X);
        Record->SetNumberField(TEXT("height"), Item.Size.Y);
        Record->SetNumberField(TEXT("bytes"), Packed.Num());
        Record->SetNumberField(TEXT("sourceRowPitchPixels"), Pitch);
        Record->SetNumberField(TEXT("sourceBufferHeight"), BufferHeight);
        Record->SetStringField(TEXT("gpuFormat"), GPixelFormats[Item.Format].Name);
        Record->SetStringField(TEXT("dtype"), Item.Format == PF_FloatRGBA ? TEXT("<f2") : TEXT("<f4"));
        Record->SetNumberField(TEXT("channels"), Item.Format == PF_R32_FLOAT ? 1 : 4);
        Files.Add(MakeShared<FJsonValueObject>(Record));
        if (Item.Name == TEXT("scene-data"))
            Metadata->SetNumberField(TEXT("preExposure"), reinterpret_cast<const float*>(Packed.GetData())[2]);
    }
    Metadata->SetArrayField(TEXT("files"), Files);
    Metadata->SetStringField(TEXT("status"), TEXT("complete"));
    FString Json;
    if (!FJsonSerializer::Serialize(Metadata.ToSharedRef(), TJsonWriterFactory<>::Create(&Json)) ||
        !FFileHelper::SaveStringToFile(Json, *FPaths::Combine(Directory, TEXT("metadata.tmp")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM) ||
        !IFileManager::Get().Move(*FPaths::Combine(Directory, TEXT("metadata.json")), *FPaths::Combine(Directory, TEXT("metadata.tmp"))))
    { Fail(TEXT("Cannot commit completion metadata")); return; }
    bFinished = true;
    UE_LOG(LogTemp, Display, TEXT("UEFog readback complete: %s"), *Directory);
}
