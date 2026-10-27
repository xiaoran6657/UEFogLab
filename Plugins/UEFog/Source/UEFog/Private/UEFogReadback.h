#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "RHIGPUReadback.h"
#include "RenderGraphResources.h"

/** Explicit diagnostic mode only. One frame, asynchronous texture copies, packed rows. */
class FUEFogReadback
{
public:
    FUEFogReadback();
    bool WantsFrame();
    void Add(FRDGBuilder& GraphBuilder, FRDGTextureRef Texture, const FString& Name);
    void Poll();
    TSharedPtr<FJsonObject> Metadata;
private:
    struct FItem
    {
        FString Name;
        FIntPoint Size;
        EPixelFormat Format;
        TUniquePtr<FRHIGPUTextureReadback> Readback;
    };
    void Fail(const FString& Reason);
    FString Directory;
    int32 TargetFrame = 30;
    int32 SeenFrames = 0;
    bool bScheduled = false;
    bool bFinished = false;
    TArray<FItem> Items;
};
