#include "UEFogLighting.h"
#include "HAL/IConsoleManager.h"
#include "Misc/CommandLine.h"
#include "Misc/DefaultValueHelper.h"
#include "Misc/Parse.h"

static TAutoConsoleVariable<int32> Model(TEXT("r.UEFog.LightingModel"), 0, TEXT("0: Prescribed; 1: manual directional HG v1."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> Albedo(TEXT("r.UEFog.Albedo"), .8f, TEXT("Scattering / extinction [0,1]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> G(TEXT("r.UEFog.HGg"), .5f, TEXT("HG asymmetry [-.8,.8]; positive is photon-forward."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> ER(TEXT("r.UEFog.IncidentR"), 4.f, TEXT("Relative linear angular-integrated incident weight, R [0,16]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> EG(TEXT("r.UEFog.IncidentG"), 3.f, TEXT("Incident weight G [0,16]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> EB(TEXT("r.UEFog.IncidentB"), 2.f, TEXT("Incident weight B [0,16]."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> KX(TEXT("r.UEFog.PhotonX"), -1.f, TEXT("Photon propagation X; normalized before use."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> KY(TEXT("r.UEFog.PhotonY"), -1.f, TEXT("Photon propagation Y."), ECVF_RenderThreadSafe);
static TAutoConsoleVariable<float> KZ(TEXT("r.UEFog.PhotonZ"), .15f, TEXT("Photon propagation Z."), ECVF_RenderThreadSafe);

static bool Number(const FString& Text, double& Out)
{
    return FDefaultValueHelper::ParseDouble(Text, Out) && FMath::IsFinite(Out);
}

static bool Vector(const FString& Text, FVector& Out)
{
    TArray<FString> Parts;
    Text.ParseIntoArray(Parts, TEXT(","), false);
    return Parts.Num() == 3 && Number(Parts[0], Out.X) && Number(Parts[1], Out.Y) && Number(Parts[2], Out.Z);
}

static bool Normalize(FVector& V)
{
    if (!FMath::IsFinite(V.X) || !FMath::IsFinite(V.Y) || !FMath::IsFinite(V.Z)) return false;
    const double Scale = FMath::Max3(FMath::Abs(V.X), FMath::Abs(V.Y), FMath::Abs(V.Z));
    if (Scale == 0) return false;
    V /= Scale;
    V /= V.Size();
    return true;
}

static void SetNumber(IConsoleVariable* Variable, double Value)
{
    // IConsoleVariable::Set(float) formats with %g (six significant digits).
    // Preserve the request until the CVar's single intentional float32 cast.
    Variable->Set(*FString::Printf(TEXT("%.17g"), Value), ECVF_SetByCommandline);
}

bool ApplyUEFogLightingLaunch(FString& Error)
{
    FString Text;
    double LaunchModel = 0;
    if (FParse::Value(FCommandLine::Get(), TEXT("FogLabLightingModel="), Text, false) &&
        (!Number(Text, LaunchModel) || (LaunchModel != 0 && LaunchModel != 1)))
    { Error = TEXT("FogLabLightingModel must be 0 or 1"); return false; }
    const TCHAR* Keys[] = { TEXT("FogLabAlbedo="), TEXT("FogLabHGg="), TEXT("FogLabIncidentWeight="), TEXT("FogLabPhotonDirection=") };
    if (LaunchModel == 0)
    {
        for (const TCHAR* Key : Keys) if (FParse::Value(FCommandLine::Get(), Key, Text, false))
        { Error = TEXT("Explicit lighting parameters require ManualDirectionalHG"); return false; }
    }
    double A = .8, PhaseG = .5;
    FVector E(4,3,2), K(-1,-1,.15);
    if (FParse::Value(FCommandLine::Get(), Keys[0], Text, false) && !Number(Text,A))
    { Error = TEXT("Invalid albedo"); return false; }
    if (FParse::Value(FCommandLine::Get(), Keys[1], Text, false) && !Number(Text,PhaseG))
    { Error = TEXT("Invalid HG g"); return false; }
    if (FParse::Value(FCommandLine::Get(), Keys[2], Text, false) && !Vector(Text,E))
    { Error = TEXT("Incident weight requires three finite CSV numbers"); return false; }
    if (FParse::Value(FCommandLine::Get(), Keys[3], Text, false) && !Vector(Text,K))
    { Error = TEXT("Photon direction requires three finite CSV numbers"); return false; }
    if (A < 0 || A > 1 || FMath::Abs(PhaseG) > .8 || E.GetMin() < 0 || E.GetMax() > 16 || !Normalize(K))
    { Error = TEXT("Lighting parameters outside M2-v1 domain"); return false; }
    Model->Set(int32(LaunchModel), ECVF_SetByCommandline);
    SetNumber(Albedo.operator->(), A); SetNumber(G.operator->(), PhaseG);
    SetNumber(ER.operator->(), E.X); SetNumber(EG.operator->(), E.Y); SetNumber(EB.operator->(), E.Z);
    SetNumber(KX.operator->(), K.X); SetNumber(KY.operator->(), K.Y); SetNumber(KZ.operator->(), K.Z);
    return true;
}

bool GetUEFogLightingSnapshot(FUEFogLighting& Out, FString& Error)
{
    Out.Model = Model.GetValueOnRenderThread();
    if (Out.Model == 0) return true;
    if (Out.Model != 1) { Error = TEXT("Invalid lighting model"); return false; }
    Out.Albedo = Albedo.GetValueOnRenderThread(); Out.G = G.GetValueOnRenderThread();
    Out.IncidentWeight = FVector3f(ER.GetValueOnRenderThread(), EG.GetValueOnRenderThread(), EB.GetValueOnRenderThread());
    FVector K(KX.GetValueOnRenderThread(), KY.GetValueOnRenderThread(), KZ.GetValueOnRenderThread());
    const FVector3f E = Out.IncidentWeight;
    if (!FMath::IsFinite(Out.Albedo) || Out.Albedo < 0 || Out.Albedo > 1 ||
        !FMath::IsFinite(Out.G) || FMath::Abs(Out.G) > .8f ||
        !FMath::IsFinite(E.X) || !FMath::IsFinite(E.Y) || !FMath::IsFinite(E.Z) ||
        E.GetMin() < 0 || E.GetMax() > 16 || !Normalize(K))
    { Error = TEXT("Invalid render-thread lighting snapshot; refusing silent fallback/clamp"); return false; }
    Out.PhotonDirection = FVector3f(K);
    return true;
}
