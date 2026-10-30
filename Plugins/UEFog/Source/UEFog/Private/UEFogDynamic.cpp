#include "UEFogDynamic.h"
#include "UEFogCompare.h"
#include "SceneView.h"
#include "Misc/ScopeLock.h"

namespace
{
FUEFogDynamicState GameState;
TAtomic<bool> Active(false);
FCriticalSection StateLock;
TMap<uint32, FUEFogDynamicState> Views;
}

bool IsUEFogDynamicActive()
{
    return Active.Load();
}

void PublishUEFogDynamicState(const FUEFogDynamicState& State)
{
    check(IsInGameThread());
    GameState = State;
    Active.Store(true);
}

void BindUEFogDynamicView(const FSceneViewFamily& Family)
{
    check(IsInGameThread());
    if (!IsUEFogDynamicActive()) return;
    if (Family.Views.Num() != 1 || !Family.Views[0]->bIsGameView || Family.FrameNumber == UINT_MAX || GameState.ActorJson.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("Dynamic capture requires one initialized game view"));
        FPlatformMisc::RequestExitWithStatus(false, 5);
        return;
    }
    FScopeLock Lock(&StateLock);
    // A bounded queue, never RT reads of actors or the mutable GT state.
    if (Views.Num() > 8) Views.Empty();
    Views.Add(Family.FrameNumber, GameState);
}

bool GetUEFogDynamicState(uint32 Frame, FUEFogDynamicState& State)
{
    FScopeLock Lock(&StateLock);
    if (const auto* Found = Views.Find(Frame)) { State = *Found; return true; }
    return false;
}
