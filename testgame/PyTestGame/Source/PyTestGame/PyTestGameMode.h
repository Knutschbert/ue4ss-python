#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "PyTestGameMode.generated.h"

// The game mode of the test game. When the map starts it spawns a few populated APyTestActor instances (they reference
// each other) so the Python checks have known data: arrays, maps, sets, weak and lazy pointers, delegates, interfaces.
UCLASS()
class PYTESTGAME_API APyTestGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	APyTestGameMode();

	virtual void BeginPlay() override;

	UPROPERTY() int32 SpawnedCount = 0;

	// A Blueprint class of the engine (BP_Sky_Sphere: plain functions, an event graph, calls between its own functions), referenced so
	// that it is cooked into the test game and spawned with the test actors; the Blueprint hook tests run on it.
	UPROPERTY() TSubclassOf<AActor> SkySphereClass;
};
