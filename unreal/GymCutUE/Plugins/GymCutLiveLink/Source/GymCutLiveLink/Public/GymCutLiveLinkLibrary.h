// Script access (Python / Blueprint) to the GymCut Live Link source.
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"

#include "GymCutLiveLinkLibrary.generated.h"

UCLASS()
class GYMCUTLIVELINK_API UGymCutLiveLinkLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Add (or keep) the GymCut UDP source listening on Port. Returns true when it is running. */
	UFUNCTION(BlueprintCallable, Category = "GymCut|Live Link")
	static bool StartGymCutSource(int32 Port = 54322);

	/** Remove the GymCut source from Live Link. */
	UFUNCTION(BlueprintCallable, Category = "GymCut|Live Link")
	static void StopGymCutSource();

	/** "running port=54322 frames=123" or "stopped". */
	UFUNCTION(BlueprintPure, Category = "GymCut|Live Link")
	static FString GetGymCutSourceStatus();
};
