#pragma once

#include "CoreMinimal.h"

namespace UEFogMath
{
    // Constant source function J = q / sigma_t, no emission in vacuum.
    inline double HomogeneousTransmittance(double ExtinctionPerMetre, double DistanceMetres)
    {
        return FMath::Exp(-FMath::Max(0.0, ExtinctionPerMetre) * FMath::Max(0.0, DistanceMetres));
    }

    inline bool IntersectSphere(const FVector3d& Origin, const FVector3d& UnitDirection,
        const FVector3d& Centre, double Radius, double& Enter, double& Exit)
    {
        const FVector3d Offset = Origin - Centre;
        const double B = FVector3d::DotProduct(Offset, UnitDirection);
        const double Discriminant = B * B - (Offset.SquaredLength() - Radius * Radius);
        if (Discriminant < 0.0 || Radius <= 0.0) return false;
        const double Root = FMath::Sqrt(Discriminant);
        Enter = FMath::Max(0.0, -B - Root);
        Exit = -B + Root;
        return Exit > Enter;
    }
}
