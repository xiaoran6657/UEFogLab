#pragma once

#include "Containers/UnrealString.h"

struct FUEFogMobileCoverageLayout
{
    int32 StripeCount;
    int32 FirstColumn;
    int32 HalfHeightRows = 60;
};

inline FUEFogMobileCoverageLayout UEFogMobileCoverageLayout(const FString& Fixture)
{
    if (Fixture == TEXT("RepairCoverage1")) return {1, 1000};
    if (Fixture == TEXT("RepairCoverageAligned")) return {1, 1001};
    if (Fixture == TEXT("RepairCoverage16")) return {16, 984};
    if (Fixture == TEXT("RepairCoverage128")) return {128, 872};
    if (Fixture == TEXT("RepairCoverageWide432")) return {632, 144, 216};
    if (Fixture == TEXT("RepairCoverageWide712")) return {632, 144, 356};
    return {0, 0};
}
