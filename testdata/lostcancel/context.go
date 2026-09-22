package lostcancel

import (
	"context"
	"time"
)

func Limited(parent context.Context) context.Context {
	ctx, _ := context.WithTimeout(parent, time.Second)
	return ctx
}
