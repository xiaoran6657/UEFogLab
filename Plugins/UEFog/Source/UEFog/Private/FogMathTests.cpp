#include "FogMath.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FFogAnalyticTest, "UEFogLab.Math.AnalyticMedium",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FFogAnalyticTest::RunTest(const FString& Parameters)
{
    TestEqual(TEXT("Vacuum preserves background"), UEFogMath::HomogeneousTransmittance(0.0, 7.0), 1.0);
    TestTrue(TEXT("Optical depth 1 gives exp(-1)"), FMath::IsNearlyEqual(UEFogMath::HomogeneousTransmittance(0.2, 5.0), 0.3678794411714423, 1e-12));
    const double T2 = UEFogMath::HomogeneousTransmittance(0.3, 2.0);
    const double T5 = UEFogMath::HomogeneousTransmittance(0.3, 5.0);
    TestTrue(TEXT("Segment composition is multiplicative"), FMath::IsNearlyEqual(T2 * T5, UEFogMath::HomogeneousTransmittance(0.3, 7.0), 1e-12));
    TestTrue(TEXT("More distance reduces transmittance"), T5 < T2);
    double Enter = 0.0, Exit = 0.0;
    TestTrue(TEXT("Outside ray intersects"), UEFogMath::IntersectSphere(FVector3d(-3, 0, 0), FVector3d(1, 0, 0), FVector3d::ZeroVector, 1, Enter, Exit));
    TestEqual(TEXT("Entry distance"), Enter, 2.0);
    TestEqual(TEXT("Exit distance"), Exit, 4.0);
    TestTrue(TEXT("Inside ray intersects"), UEFogMath::IntersectSphere(FVector3d::ZeroVector, FVector3d(1, 0, 0), FVector3d::ZeroVector, 1, Enter, Exit));
    TestEqual(TEXT("Inside clamps entry to camera"), Enter, 0.0);
    TestFalse(TEXT("Sphere behind camera is rejected"), UEFogMath::IntersectSphere(FVector3d(-3, 0, 0), FVector3d(-1, 0, 0), FVector3d::ZeroVector, 1, Enter, Exit));
    return true;
}
#endif
