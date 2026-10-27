#pragma once

#include "SceneViewExtension.h"
#include "UEFogReadback.h"

class FUEFogViewExtension final : public FSceneViewExtensionBase
{
public:
    explicit FUEFogViewExtension(const FAutoRegister& AutoRegister);
    virtual void SetupViewFamily(FSceneViewFamily& InViewFamily) override;
    virtual void SubscribeToPostProcessingPass(EPostProcessingPass Pass, const FSceneView& View,
        FPostProcessingPassDelegateArray& Callbacks, bool bIsPassEnabled) override;
private:
    FUEFogReadback Readback;
    FScreenPassTexture RenderFog(FRDGBuilder& GraphBuilder, const FSceneView& View,
        const FPostProcessMaterialInputs& Inputs);
};
